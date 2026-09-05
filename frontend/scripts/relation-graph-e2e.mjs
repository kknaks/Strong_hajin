import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

// 관계 탐색: a manager searches for work and follows how it came about — request → work → its parts and materials —
// then opens the work itself. Every hop is the server's authorized answer, and AX walks the same graph through MCP.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const title = `관계 탐색 업무 ${stamp}`;

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");
  const navigation = page.getByRole("navigation", { name: "제품 탐색" });
  await navigation.getByRole("button", { name: "내 업무" }).click();

  // A request that mina sent and jiho accepted, with a part and an output under it.
  await page.evaluate(async (subject) => {
    await fetch("/api/work-requests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: subject, assignee_id: "jiho" }),
    });
  }, title);

  await page.getByRole("button", { name: "로그아웃" }).click();
  await loginAs(page, "jiho");
  const judgement = await pollFor(
    page,
    () =>
      page.evaluate(async (subject) => {
        const items = await (await fetch("/api/action-items")).json();
        return items.find((item) => item.subject === subject) ?? null;
      }, title),
    { timeout: 20_000, description: "the request to reach the assignee" },
  );
  const built = await page.evaluate(async ({ actionItemId, expectedVersion, subject }) => {
    await fetch(`/api/action-items/${actionItemId}/commands/accept`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: expectedVersion }),
    });
    const work = await (await fetch("/api/my-work")).json();
    const task = work.find((row) => row.title === subject);
    const child = await (await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: `${subject} 하위`, parent_task_id: task.task_id }),
    })).json();
    await fetch(`/api/tasks/${task.task_id}/materials/links`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "output", url: "https://docs.example.com/graph", label: `결과 문서 ${subject}` }),
    });
    return { taskId: task.task_id, childId: child.task_id };
  }, { actionItemId: judgement.action_item_id, expectedVersion: judgement.expected_version, subject: title });

  // The manager follows it from the search box.
  await page.reload({ waitUntil: "domcontentloaded" });
  await navigation.getByRole("button", { name: "관계 탐색" }).click();
  await page.getByLabel("무엇을 찾을까요").fill(title);
  await page.getByRole("button", { name: "찾기" }).click();
  const results = page.locator("section[aria-label='검색 결과']");
  await results.locator(`li[data-node^="task:"]`).first().getByRole("button").click();

  const around = page.locator("section[aria-label='연결']");
  await around.locator("li").first().waitFor({ timeout: 20_000 });
  const connections = ((await around.textContent()) ?? "").replace(/\s+/g, " ");
  for (const expected of ["이 업무를 만든 요청", "담당", "하위 업무", "참고 자료·산출물"]) {
    if (!connections.includes(expected)) throw new Error(`a connection is missing (${expected}): ${connections}`);
  }
  if (!connections.includes("지호")) throw new Error(`the holder is not named: ${connections}`);

  // One more hop, to the request that made it, and then back to the work itself.
  await around.locator('li[data-edge="produced"]').first().getByRole("button").click();
  await page.waitForFunction(
    () => document.querySelector("section[aria-label='연결'] h4")?.textContent?.includes("업무 요청"),
    undefined,
    { timeout: 20_000 },
  );
  await around.locator('li[data-node^="task:"]').first().getByRole("button").click();
  await around.getByRole("button", { name: "원본 업무 열기" }).click();
  const drawer = page.getByRole("dialog", { name: "업무 상세" });
  await drawer.waitFor({ timeout: 20_000 });
  if (!((await drawer.textContent()) ?? "").includes(title)) throw new Error("the graph did not open the work it named");
  await drawer.getByRole("button", { name: "상세 닫기" }).click();

  // Someone who may not read this work finds none of it, and cannot walk into it.
  await page.getByRole("dialog").waitFor({ state: "detached", timeout: 20_000 });
  await page.getByRole("button", { name: "로그아웃" }).click();
  await loginAs(page, "mina");
  const hidden = await page.evaluate(async ({ childId, subject }) => {
    const found = await (await fetch(`/api/graph/search?q=${encodeURIComponent(subject + " 하위")}`)).json();
    const walked = await fetch(`/api/graph/neighbors?node=task:${childId}`);
    return { nodes: found.nodes.length, status: walked.status, body: await walked.text() };
  }, { childId: built.childId, subject: title });
  if (hidden.nodes !== 0 || ![403, 404].includes(hidden.status) || hidden.body.includes("하위")) {
    throw new Error(`work someone may not read leaked into the graph: ${JSON.stringify(hidden)}`);
  }

  await page.screenshot({ path: "test-results/relation-graph-e2e.png", fullPage: true });
  console.log(JSON.stringify({ result: "a manager followed request → work → parts and materials, and opened the work", task_id: built.taskId }));
} finally {
  await browser.close();
}
