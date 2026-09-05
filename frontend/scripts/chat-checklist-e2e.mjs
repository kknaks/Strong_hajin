import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

// AX에 단계를 부탁하고, 사람이 승인한다. A delegated turn calls the real checklist tool, writes nothing, and the
// person sees a card naming the work and the step it would add. Only their approval puts it on the list — once —
// and the Task detail they already had open shows it.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const taskTitle = `AX 체크리스트 업무 ${stamp}`;
const stepText = `납품 일정 확인 ${stamp}`;

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

  const task = await page.evaluate(async (title) => {
    const created = await (await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    })).json();
    await fetch(`/api/tasks/${created.task_id}/checklist`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: "자료 모으기" }),
    });
    return created;
  }, taskTitle);

  await page.getByRole("button", { name: "AX" }).click();
  const createConversation = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  const conversation = await (await createConversation).json();
  await page.getByLabel("AX 메시지").fill(
    [
      `SCAX MCP의 task_checklist_list 도구를 task_id ${task.task_id}로 실제 호출해 현재 단계를 확인해줘.`,
      `그 다음 같은 턴에서 task_checklist_add 도구를 실제로 호출해 '${stepText}' 단계를 추가 제안해줘.`,
      "두 도구를 모두 실제로 호출하고, 답변으로만 제안하지 마. 내가 화면에서 승인할 때까지 기다려.",
    ].join(" "),
  );
  await page.getByRole("button", { name: "보내기" }).click();

  const pending = await pollFor(
    page,
    () =>
      page.evaluate(async (conversationId) => {
        const response = await fetch(`/api/conversations/${conversationId}`);
        if (!response.ok) return null;
        const current = await response.json();
        return current.actions?.find((item) => item.action_type === "task.checklist.add" && item.state === "pending") ?? null;
      }, conversation.conversation_id),
    { timeout: 120_000, description: "the pending task.checklist.add ActionItem" },
  );

  // Nothing was written by the turn itself.
  const before = await page.evaluate(async (taskId) => (await (await fetch(`/api/tasks/${taskId}`)).json()).checklist.map((row) => row.text), task.task_id);
  if (before.join("|") !== "자료 모으기") throw new Error(`the turn changed the list before anyone approved: ${JSON.stringify(before)}`);

  // The card names the work and the step, from the server's presentation.
  if (pending.subject !== taskTitle) throw new Error(`card subject is not the work: ${JSON.stringify(pending.subject)}`);
  if (pending.operation_label !== "체크리스트 단계 추가") throw new Error(`card operation: ${JSON.stringify(pending.operation_label)}`);
  const card = page.locator(`.ax-action-card[data-action-id="${pending.action_id}"]`);
  await card.waitFor({ timeout: 20_000 });
  const cardText = ((await card.textContent()) ?? "").replace(/\s+/g, " ");
  if (!cardText.includes(stepText)) throw new Error(`the card does not say what would be added: ${JSON.stringify(cardText)}`);

  await card.getByRole("button", { name: "승인" }).click();
  await card.locator("small.approved").waitFor({ timeout: 20_000 });

  // Exactly once, on the list, and readable on the Task the person opens.
  const after = await page.evaluate(async ({ taskId, actionId }) => {
    const [task, actions] = await Promise.all([
      fetch(`/api/tasks/${taskId}`).then((response) => response.json()),
      fetch("/api/actions").then((response) => response.json()),
    ]);
    return {
      steps: task.checklist.map((row) => row.text),
      version: task.version,
      action: actions.find((item) => item.action_id === actionId)?.state,
    };
  }, { taskId: task.task_id, actionId: pending.action_id });
  if (after.steps.filter((text) => text === stepText).length !== 1) {
    throw new Error(`the approved step did not land exactly once: ${JSON.stringify(after.steps)}`);
  }
  if (after.action !== "approved") throw new Error(`the action is ${after.action}`);

  // Put the chat away first: it covers the list a person would click through.
  await page.getByRole("complementary", { name: "AX 대화" }).getByRole("button", { name: "닫기" }).click();
  await navigation.getByRole("button", { name: "내 업무" }).click();
  await page.getByRole("row", { name: new RegExp(taskTitle) }).click();
  const drawer = page.getByRole("dialog", { name: "업무 상세" });
  const checklist = drawer.locator('section[aria-label="체크리스트"]');
  await checklist.locator(".checklist-item", { hasText: stepText }).waitFor({ timeout: 20_000 });

  // The history says a person did it, and that it came through the confirmation they approved.
  const historySection = drawer.locator("section[aria-label='활동·이력']");
  const openHistory = historySection.getByRole("button", { name: "이력 보기" });
  await openHistory.scrollIntoViewIfNeeded();
  await openHistory.click();
  const line = historySection.locator("ol.activity-list > li", { hasText: "체크리스트 추가" }).first();
  await line.waitFor({ timeout: 20_000 });
  const lineText = ((await line.textContent()) ?? "").replace(/\s+/g, " ");
  if (!lineText.includes("민아")) throw new Error(`the history does not name the person: ${JSON.stringify(lineText)}`);
  if (!lineText.includes("AX를 통해")) throw new Error(`the history does not say it came through AX: ${JSON.stringify(lineText)}`);

  await page.screenshot({ path: "test-results/chat-checklist-e2e.png", fullPage: true });
  console.log(JSON.stringify({ result: "AX proposed a checklist step, a person approved it, and it landed once", task_id: task.task_id, action_id: pending.action_id, steps: after.steps, task_version: after.version }));
} finally {
  await browser.close();
}
