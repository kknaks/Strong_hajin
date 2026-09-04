import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

// Approving an AX Action from the chat: the card shows the real work title with the server-provided preview rows,
// the canonical effect runs once, and the current My Work surface re-reads its projection and shows the created task
// without navigating away or losing the chosen view filter.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const taskTitle = `AX 승인 생성 업무 ${stamp}`;
const sourceTitle = `AX 승인 근거 업무 ${stamp}`;
const fileName = `승인근거-${stamp}.md`;
const fileBody = ["# 승인 근거", "", "공급사는 한빛상사이고 납기일은 2026-09-30입니다.", "", ...Array.from({ length: 20 }, (_, index) => `부속 항목 ${index + 1}: 표준 사양, 단가 협의 완료.`)].join("\n");

const browser = await chromium.launch({
  executablePath:
    process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");
  const navigation = page.getByRole("navigation", { name: "제품 탐색" });
  await navigation.getByRole("button", { name: "내 업무" }).click();
  const filter = page.locator("#task-state-filter");
  await filter.waitFor();
  await filter.selectOption("all");

  // A Task with an indexed attachment, so the proposing turn can actually read evidence to link on the card.
  const sourceTask = await page.evaluate(async (title) => {
    const response = await fetch("/api/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) });
    return response.json();
  }, sourceTitle);
  const uploaded = await page.evaluate(
    async ({ taskId, name, body }) => {
      const form = new FormData();
      form.append("kind", "input");
      form.append("file", new File([body], name, { type: "text/markdown" }), name);
      const response = await fetch(`/api/tasks/${taskId}/materials`, { method: "POST", body: form });
      return { status: response.status, body: await response.json() };
    },
    { taskId: sourceTask.task_id, name: fileName, body: fileBody },
  );
  if (uploaded.status !== 201) throw new Error(`upload failed: ${JSON.stringify(uploaded)}`);
  await pollFor(
    page,
    () =>
      page.evaluate(async (taskId) => {
        const items = await (await fetch(`/api/tasks/${taskId}/materials`)).json();
        return items[0]?.extraction?.status === "completed" ? items[0] : null;
      }, sourceTask.task_id),
    { timeout: 30_000, description: "the material worker to index the approval evidence" },
  );

  await page.getByRole("button", { name: "AX" }).click();
  const createConversation = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  const conversation = await (await createConversation).json();
  await page.getByLabel("AX 메시지").fill(
    [
      `먼저 SCAX MCP의 task_material_search 도구를 task_id ${sourceTask.task_id}, 질의 '납기일'로 실제 호출해 첨부 내용을 확인해줘.`,
      `그 다음 같은 턴에서 task_create_self 도구를 실제로 호출해서 제목 '${taskTitle}'의 내 업무를 생성 제안해줘.`,
      "두 도구를 모두 실제로 호출하고, 답변으로만 제안하지 마.",
      "내가 화면에서 승인할 때까지 기다려.",
    ].join(" "),
  );
  await page.getByRole("button", { name: "보내기" }).click();

  const pending = await pollFor(
    page,
    () =>
      page.evaluate(async (conversationId) => {
        const response = await fetch(`/api/conversations/${conversationId}`, { headers: { "X-Demo-Persona": "mina" } });
        if (!response.ok) return null;
        const current = await response.json();
        return current.actions?.find((item) => item.action_type === "task.create_self" && item.state === "pending") ?? null;
      }, conversation.conversation_id),
    { timeout: 120_000, description: "the pending task.create_self ActionItem" },
  );
  if (pending.subject !== taskTitle || pending.operation_label !== "업무 생성") {
    throw new Error(`server presentation is missing the real title/operation: ${JSON.stringify({ subject: pending.subject, operation_label: pending.operation_label })}`);
  }
  if (!Array.isArray(pending.preview) || !pending.preview.some((row) => row.label === "담당")) {
    throw new Error(`server preview lacks the assignee row: ${JSON.stringify(pending.preview)}`);
  }

  // The chat card renders the server presentation verbatim: subject as title, operation kicker, preview rows, commands.
  const card = page.locator(`.ax-action-card[data-action-id="${pending.action_id}"]`);
  await card.waitFor({ timeout: 20_000 });
  const cardTitle = ((await card.locator("b").first().textContent()) ?? "").trim();
  if (cardTitle !== taskTitle) throw new Error(`card title is not the real work title: ${JSON.stringify(cardTitle)}`);
  const kicker = ((await card.locator(".ax-card-kicker").textContent()) ?? "").trim();
  if (!kicker.includes("업무 생성")) throw new Error(`card kicker lacks the operation label: ${JSON.stringify(kicker)}`);
  const previewRows = await card.locator(".ax-preview-row dt").allTextContents();
  if (!previewRows.includes("담당")) throw new Error(`card preview rows: ${JSON.stringify(previewRows)}`);

  // The attachment the turn actually read is linked on the card, and it names the real file.
  const turnEvidence = await page.evaluate(async ({ conversationId, turnId }) => {
    const current = await (await fetch(`/api/conversations/${conversationId}`, { headers: { "X-Demo-Persona": "mina" } })).json();
    return (current.material_evidence ?? []).filter((item) => item.turn_id === turnId).map((item) => item.name);
  }, { conversationId: conversation.conversation_id, turnId: pending.turn_id });
  if (turnEvidence.length === 0) throw new Error("the proposing turn recorded no material evidence to link");
  const evidenceRow = (pending.preview ?? []).find((row) => row.id === "evidence");
  if (!evidenceRow || !evidenceRow.value.includes(fileName)) {
    throw new Error(`server preview lacks the evidence link: ${JSON.stringify({ evidenceRow, turnEvidence })}`);
  }
  const renderedEvidence = ((await card.locator(".ax-preview-row.evidence dd").textContent()) ?? "").trim();
  if (!renderedEvidence.includes(fileName)) throw new Error(`card evidence row: ${JSON.stringify(renderedEvidence)}`);

  await page.screenshot({ path: "test-results/chat-approval-pending-e2e.png", fullPage: true });

  // Approve from the chat; the current My Work surface must re-read /api/my-work and show the task, filter intact.
  const myWorkReread = page.waitForRequest((request) => request.url().endsWith("/api/my-work"), { timeout: 20_000 });
  const decided = page.waitForResponse(
    (response) => response.url().endsWith(`/api/actions/${pending.action_id}/decide`) && response.request().method() === "POST",
  );
  await card.getByRole("button", { name: "승인" }).click();
  const decideResponse = await decided;
  if (decideResponse.status() !== 200) throw new Error(`decide returned ${decideResponse.status()}`);
  await myWorkReread;
  const taskCard = page.locator(".canvas .task-card, .canvas .task-row, .canvas tr", { hasText: taskTitle }).first();
  await taskCard.waitFor({ timeout: 20_000 });
  if ((await filter.inputValue()) !== "all") throw new Error("the My Work filter was reset (page remounted)");
  if ((await page.getByText("판단은 저장되었지만 화면을 갱신하지 못했습니다.").count()) !== 0) {
    throw new Error("projection refresh reported a failure after a successful approval");
  }
  await card.locator("small.approved").waitFor({ timeout: 10_000 });

  // Exactly one canonical effect: the ledger holds one task with that title and the Action is approved once.
  const ledger = await page.evaluate(async ({ actionId, title }) => {
    const headers = { "X-Demo-Persona": "mina" };
    const [work, actions] = await Promise.all([
      fetch("/api/my-work", { headers }).then((response) => response.json()),
      fetch("/api/actions", { headers }).then((response) => response.json()),
    ]);
    return {
      matching: work.filter((task) => task.title === title).length,
      action: actions.find((item) => item.action_id === actionId),
    };
  }, { actionId: pending.action_id, title: taskTitle });
  if (ledger.matching !== 1 || ledger.action?.state !== "approved" || !ledger.action?.result?.task_id) {
    throw new Error(`canonical effect mismatch: ${JSON.stringify(ledger)}`);
  }

  await page.screenshot({ path: "test-results/chat-approval-e2e.png", fullPage: true });
  console.log(
    JSON.stringify({
      result: "chat approval executed once and the current My Work projection re-read in place",
      action_id: pending.action_id,
      task_id: ledger.action.result.task_id,
      preview_rows: previewRows,
      evidence: renderedEvidence,
    }),
  );
} finally {
  await browser.close();
}
