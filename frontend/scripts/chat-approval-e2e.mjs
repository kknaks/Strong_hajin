import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

// Approving an AX Action from the chat: the card shows the real work title with the server-provided preview rows,
// the canonical effect runs once, and the current My Work surface re-reads its projection and shows the created task
// without navigating away or losing the chosen view filter.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const taskTitle = `AX 승인 생성 업무 ${Date.now()}`;

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

  await page.getByRole("button", { name: "AX" }).click();
  const createConversation = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  const conversation = await (await createConversation).json();
  await page.getByLabel("AX 메시지").fill(
    [
      `SCAX MCP의 task_create_self 도구를 실제로 호출해서 제목 '${taskTitle}'의 내 업무를 생성 제안해줘.`,
      "반드시 도구를 호출해 ActionItem을 저장하고, 답변으로만 제안하지 마.",
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
    }),
  );
} finally {
  await browser.close();
}
