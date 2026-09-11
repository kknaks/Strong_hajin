import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

// One natural work-log sentence → one frozen approval card → three existing Task effects, exactly once.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const cpaTaskTitle = `오늘 A병원 업무 ${stamp}`;
const placeTaskTitle = `플레이스 순위 ${stamp}`;
const instagramTaskTitle = `인스타 체험단 ${stamp}`;

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");
  const seeded = await page.evaluate(async ({ cpaTaskTitle, placeTaskTitle, instagramTaskTitle }) => {
    const create = async (title, checklist = []) => {
      const response = await fetch("/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, checklist }),
      });
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    };
    const [cpa, place, instagram, report] = await Promise.all([
      create(cpaTaskTitle, ["CPA 데이터 취합"]),
      create(placeTaskTitle),
      create(instagramTaskTitle),
      fetch("/api/daily-reports/status?report_date=2026-09-11").then((response) => response.json()),
    ]);
    return { cpa, place, instagram, report };
  }, { cpaTaskTitle, placeTaskTitle, instagramTaskTitle });

  await page.getByRole("button", { name: "AX", exact: true }).click();
  const createdConversation = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  await page.getByLabel("AX 메시지").fill(
    `${cpaTaskTitle}의 체크리스트 CPA 데이터 취합은 완료했고, ${placeTaskTitle}는 확인 중이고, ${instagramTaskTitle}은 5명 컨택했다고 일지 업데이트해줘.`,
  );
  await page.getByRole("button", { name: "보내기" }).click();
  const conversation = await (await createdConversation).json();

  const pending = await pollFor(
    page,
    () => page.evaluate(async (conversationId) => {
      const current = await fetch(`/api/conversations/${conversationId}`).then((response) => response.json());
      const batchCalls = (current.tool_invocations ?? []).filter(
        (item) => item.tool_name === "task_progress_batch" && item.state === "completed",
      );
      const action = (current.actions ?? []).find(
        (item) => item.action_type === "task.progress.batch" && item.state === "pending",
      );
      return action && batchCalls.length === 1 ? { action, toolInvocations: current.tool_invocations } : null;
    }, conversation.conversation_id),
    { timeout: 180_000, description: "one task.progress.batch proposal from the natural work-log sentence" },
  );
  if (pending.toolInvocations.some((item) => item.tool_name.startsWith("daily_report_"))) {
    throw new Error(`the work log was routed to a Report: ${JSON.stringify(pending.toolInvocations)}`);
  }

  const before = await page.evaluate(async ({ cpaId, placeId, instagramId }) => Promise.all(
    [cpaId, placeId, instagramId].map((id) => fetch(`/api/tasks/${id}`).then((response) => response.json())),
  ), { cpaId: seeded.cpa.task_id, placeId: seeded.place.task_id, instagramId: seeded.instagram.task_id });
  if (before[0].checklist[0].done || before.slice(1).some((task) => task.version !== 1)) {
    throw new Error(`the delegated turn changed Tasks before approval: ${JSON.stringify(before)}`);
  }

  const card = page.locator(`.ax-action-card[data-action-id="${pending.action.action_id}"]`);
  // A busy prior journey can leave the drawer on an older list projection even though this Turn is already
  // canonical. Re-enter through the completed conversation rather than treating that stale client selection as a
  // missing approval card.
  try {
    await card.waitFor({ timeout: 5_000 });
  } catch {
    await pollFor(
      page,
      () => page.evaluate(async (conversationId) => {
        const current = await fetch(`/api/conversations/${conversationId}`).then((response) => response.json());
        return current.messages?.some((message) => message.role === "assistant" && message.body_state === "final") ? true : null;
      }, conversation.conversation_id),
      { timeout: 30_000, description: "batch proposal answer to enter conversation history" },
    );
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("navigation", { name: "제품 탐색" }).waitFor();
    await page.getByRole("button", { name: "AX", exact: true }).click();
    await page.getByRole("button", { name: "대화 히스토리" }).click();
    await page.locator(`[data-conversation-id="${conversation.conversation_id}"]`).click();
    await card.waitFor({ timeout: 20_000 });
  }
  const cardText = ((await card.textContent()) ?? "").replace(/\s+/g, " ");
  for (const expected of [cpaTaskTitle, placeTaskTitle, instagramTaskTitle, "CPA 데이터 취합", "확인 중", "5명", "컨택"]) {
    if (!cardText.includes(expected)) throw new Error(`batch card omitted ${expected}: ${JSON.stringify(cardText)}`);
  }
  await card.screenshot({ path: "test-results/task-progress-batch-pending-e2e.png" });
  await card.getByRole("button", { name: "수정" }).click();
  await card.getByLabel(`진행 내용 - ${placeTaskTitle}`).fill("플레이스 순위 검수 중");
  await card.screenshot({ path: "test-results/task-progress-batch-editing-e2e.png" });
  await card.getByRole("button", { name: "저장" }).click();
  await card.locator("small.approved").filter({ hasText: "3건 반영됨" }).waitFor({ timeout: 20_000 });

  const ledger = await page.evaluate(async ({ ids, actionId }) => {
    const tasks = await Promise.all(ids.map((id) => fetch(`/api/tasks/${id}`).then((response) => response.json())));
    const histories = await Promise.all(ids.map((id) => fetch(`/api/tasks/${id}/history`).then((response) => response.json())));
    const report = await fetch("/api/daily-reports/status?report_date=2026-09-11").then((response) => response.json());
    const detail = await fetch(`/api/action-items/${actionId}`).then((response) => response.json());
    return { tasks, histories, report, detail };
  }, {
    ids: [seeded.cpa.task_id, seeded.place.task_id, seeded.instagram.task_id],
    actionId: pending.action.action_id,
  });
  if (!ledger.tasks[0].checklist[0].done) throw new Error("CPA checklist did not become complete");
  const expectedNotes = [["플레이스 순위", "검수 중"], ["인스타 체험단", "5명", "컨택"]];
  for (const [index, expected] of expectedNotes.entries()) {
    const line = ledger.histories[index + 1].activity.find((item) => item.event_kind === "task.progress.noted");
    if (!expected.every((token) => line?.summary.includes(token)) || line.actor?.member_id !== "mina" || line.causation?.id !== pending.action.action_id) {
      throw new Error(`progress activity lost actor, source, or text: ${JSON.stringify(line)}`);
    }
  }
  if (JSON.stringify(ledger.report) !== JSON.stringify(seeded.report)) {
    throw new Error(`the batch changed the daily Report: ${JSON.stringify({ before: seeded.report, after: ledger.report })}`);
  }
  if (
    ledger.detail.execution_result?.batch_state !== "completed"
    || ledger.detail.execution_result?.applied_count !== 3
    || ledger.detail.rounds?.length !== 2
  ) {
    throw new Error(`the Action receipt is not a full three-item result: ${JSON.stringify(ledger.detail)}`);
  }
  await card.screenshot({ path: "test-results/task-progress-batch-approved-e2e.png" });
  console.log(JSON.stringify({
    result: "one natural work-log sentence updated three existing Tasks through one approved batch",
    action_id: pending.action.action_id,
    task_ids: ledger.tasks.map((task) => task.task_id),
  }));
} finally {
  await browser.close();
}
