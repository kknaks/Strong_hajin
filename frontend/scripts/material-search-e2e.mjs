import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

/**
 * upload → durable extraction job → material worker → real Codex/MCP `material_search` → cited answer.
 * Asserts the evidence card shows the file actually read, a bounded excerpt, and an origin that serves the same bytes.
 */
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const taskTitle = `첨부 근거 업무 ${stamp}`;
const supplier = `한빛상사${stamp % 1000}`;
const fileName = `견적-${stamp}.md`;
const fileBody = [
  `# ${taskTitle} 견적 검토`,
  "",
  `공급사는 ${supplier}이고 납기일은 2026-09-30입니다.`,
  "",
  "총액은 1,200,000원이며 부가세는 별도입니다. 결제 조건은 납품 후 30일입니다.",
  "",
  ...Array.from({ length: 20 }, (_, index) => `부속 항목 ${index + 1}: 표준 사양, 단가 협의 완료.`),
].join("\n");

const browser = await chromium.launch({
  executablePath:
    process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");

  // Create the task and upload the material through the same session the browser holds.
  const task = await page.evaluate(async (title) => {
    const response = await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, description: "첨부자료 검색 acceptance" }),
    });
    return response.json();
  }, taskTitle);
  const uploaded = await page.evaluate(
    async ({ taskId, name, body }) => {
      const form = new FormData();
      form.append("kind", "input");
      form.append("file", new File([body], name, { type: "text/markdown" }), name);
      const response = await fetch(`/api/tasks/${taskId}/materials`, { method: "POST", body: form });
      return { status: response.status, body: await response.json() };
    },
    { taskId: task.task_id, name: fileName, body: fileBody },
  );
  if (uploaded.status !== 201 || uploaded.body?.extraction?.status !== "queued") {
    throw new Error(`Upload did not record a queued extraction: ${JSON.stringify(uploaded)}`);
  }

  // The separate material worker must complete the extraction; the API only shows its state.
  const indexed = await pollFor(
    page,
    () =>
      page.evaluate(async (taskId) => {
        const response = await fetch(`/api/tasks/${taskId}/materials`);
        const items = await response.json();
        const item = items[0];
        return item?.extraction?.status === "completed" ? item : null;
      }, task.task_id),
    { timeout: 30_000, description: "the material worker to complete extraction" },
  );

  // The Task drawer shows the searchable state to the user before AX is asked.
  await page.getByRole("navigation", { name: "제품 탐색" }).getByRole("button", { name: "내 업무" }).click();
  await page.getByRole("row", { name: new RegExp(taskTitle) }).click();
  const drawer = page.getByRole("dialog", { name: "업무 상세" });
  await drawer.locator(`.extraction-status[data-status="completed"]`).first().waitFor({ timeout: 10_000 });
  await drawer.getByRole("button", { name: /AX에게 이 업무 묻기/ }).click();

  const createConversationResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  const conversation = await (await createConversationResponse).json();
  await page.getByLabel("AX 메시지").fill(
    [
      `업무 '${taskTitle}'(task_id ${task.task_id})를 첨부자료까지 포함해 설명해줘.`,
      "반드시 SCAX MCP의 material_search 도구를 실제로 호출해 첨부 내용을 근거로 답해.",
      "공급사 이름과 납기일을 첨부에서 찾은 대로 인용하고, 첨부에 없는 내용은 없다고 말해.",
    ].join(" "),
  );
  await page.getByRole("button", { name: "보내기" }).click();

  const cited = await pollFor(
    page,
    () =>
      page.evaluate(async (conversationId) => {
        const response = await fetch(`/api/conversations/${conversationId}`);
        if (!response.ok) return null;
        const current = await response.json();
        const turn = current.turns.at(-1);
        if (!turn || turn.state !== "completed") return null;
        const tool = current.tool_invocations.find((item) => item.tool_name === "material_search" && item.state === "completed");
        const evidence = (current.material_evidence ?? []).filter((item) => item.turn_id === turn.turn_id);
        if (!tool || evidence.length === 0) return null;
        const answer = current.messages.find((item) => item.role === "assistant" && item.turn_id === turn.turn_id);
        return { tool, evidence, answer: answer?.body ?? "" };
      }, conversation.conversation_id),
    { timeout: 180_000, description: "a completed turn with a real material_search call and recorded evidence" },
  );

  if (cited.evidence.some((item) => item.name !== fileName || item.material_id !== indexed.material_id)) {
    throw new Error("Evidence cites a material other than the uploaded file");
  }
  if (!cited.evidence.some((item) => item.excerpt.includes(supplier) || item.excerpt.includes("납기일"))) {
    throw new Error("Evidence excerpt does not contain the retrieved facts");
  }
  if (cited.evidence.some((item) => item.excerpt.length > 400)) {
    throw new Error("Evidence excerpt is not bounded");
  }
  if (!cited.answer.includes(supplier)) {
    throw new Error("Assistant answer did not cite the supplier found in the material");
  }
  if (cited.tool.result_summary?.includes(supplier) || cited.tool.input_summary.includes(fileBody.slice(0, 30))) {
    throw new Error("Tool timeline summary leaked material content");
  }

  // The evidence card is visible in the drawer and its origin serves the uploaded bytes to the authorized user.
  // 인용은 답에 붙은 근거 줄 안에 있다: 답이 먼저 읽히고, 한 번 펼치면 읽은 구간이 그대로 나온다.
  const grounds = page.locator("details.ax-answer-evidence").last();
  await grounds.waitFor({ timeout: 20_000 });
  if ((await grounds.locator("summary").textContent())?.includes("인용") !== true) {
    throw new Error("the one-line evidence bar did not count the quoted passages");
  }
  await grounds.locator("summary").click();
  const card = page.locator(`.ax-evidence-card[data-material-id="${indexed.material_id}"]`).first();
  await card.waitFor({ timeout: 20_000 });
  await card.getByText(fileName).waitFor();
  const originHref = await card.getByRole("link", { name: "원본 열기" }).getAttribute("href");
  const origin = await page.evaluate(async (href) => {
    const response = await fetch(href);
    return { status: response.status, text: await response.text() };
  }, originHref);
  if (origin.status !== 200 || !origin.text.includes(supplier)) {
    throw new Error("Origin link did not serve the uploaded material");
  }

  await page.screenshot({ path: "test-results/material-search-e2e.png", fullPage: true });
  console.log(
    JSON.stringify({
      result: "material extracted by the worker, searched by Codex through MCP, and cited with evidence cards",
      task_id: task.task_id,
      material_id: indexed.material_id,
      extraction_id: indexed.extraction.extraction_id,
      conversation_id: conversation.conversation_id,
      evidence_count: cited.evidence.length,
      tool_call: cited.tool.provider_call_id,
    }),
  );
} finally {
  await browser.close();
}
