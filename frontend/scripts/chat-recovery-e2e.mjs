import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

// 멈춘 대화도 잃지 않는다 — stopping a running turn keeps what was already said, trying again makes a new turn that
// says where it came from, and each conversation keeps its own unsent draft across switching and reloading.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");
  await page.getByRole("button", { name: "AX", exact: true }).click();

  await page.getByRole("button", { name: "새 AX 대화" }).click();

  // A request that takes long enough to stop mid-flight.
  await page.getByLabel("AX 메시지").fill(
    `SCAX MCP의 my_task_list와 work_request_list를 차례로 실제 호출한 뒤, 오늘 할 일을 길게 정리해줘. ${stamp}`,
  );
  const created = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "보내기" }).click();
  const first = await (await created).json();

  const rail = page.locator(".ax-rail").first();
  await rail.waitFor({ timeout: 30_000 });
  await pollFor(
    page,
    () =>
      page.evaluate(async (conversationId) => {
        const detail = await (await fetch(`/api/conversations/${conversationId}`)).json();
        const turn = detail.turns[0];
        return ["preparing", "tool_running", "composing"].includes(turn?.progress_state) ? turn : null;
      }, first.conversation_id),
    { timeout: 60_000, description: "the turn to actually start running" },
  );

  await page.getByRole("button", { name: "실행 취소" }).click();
  const stopped = await pollFor(
    page,
    () =>
      page.evaluate(async (conversationId) => {
        const detail = await (await fetch(`/api/conversations/${conversationId}`)).json();
        const turn = detail.turns[0];
        if (turn?.state !== "cancelled") return null;
        const assistant = detail.messages.filter((message) => message.role === "assistant" && message.turn_id === turn.turn_id);
        return { state: turn.state, progress: turn.progress_state, bodies: assistant.map((message) => [message.body_state, message.body.length]) };
      }, first.conversation_id),
    { timeout: 30_000, description: "the turn to come to a stop" },
  );
  if (stopped.progress !== "cancelled") throw new Error(`unexpected progress after cancel: ${JSON.stringify(stopped)}`);
  if (stopped.bodies.some(([state]) => state !== "cancelled")) throw new Error(`partial text lost its state: ${JSON.stringify(stopped)}`);

  // The screen says it stopped, and offers to try again.
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "AX", exact: true }).click();
  const railAfter = page.locator(".ax-rail").first();
  await railAfter.waitFor({ timeout: 30_000 });
  if (railAfter.getAttribute("data-progress") === "running") throw new Error("a stopped turn still looks like it is running");
  await page.getByRole("button", { name: "다시 시도" }).first().click();
  const retried = await pollFor(
    page,
    () =>
      page.evaluate(async (conversationId) => {
        const detail = await (await fetch(`/api/conversations/${conversationId}`)).json();
        return detail.turns.length > 1 ? detail.turns[1] : null;
      }, first.conversation_id),
    { timeout: 30_000, description: "the retry to open a new turn" },
  );
  if (!retried.retry_of_turn_id) throw new Error("the retry does not say what it came from");
  await pollFor(
    page,
    () => page.evaluate(async (conversationId) => {
      const detail = await (await fetch(`/api/conversations/${conversationId}`)).json();
      return detail.turns[1]?.state === "completed";
    }, first.conversation_id),
    { timeout: 120_000, description: "the retried conversation to earn a history entry" },
  );

  // Each conversation keeps its own unsent draft, across switching and a full reload.
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  await page.getByLabel("AX 메시지").fill(`둘째 대화를 시작해줘 ${stamp}`);
  const secondCreated = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "보내기" }).click();
  const second = await (await secondCreated).json();
  await pollFor(
    page,
    () => page.evaluate(async (conversationId) => {
      const detail = await (await fetch(`/api/conversations/${conversationId}`)).json();
      return detail.turns[0]?.state === "completed";
    }, second.conversation_id),
    { timeout: 120_000, description: "the second conversation's first answer" },
  );
  await page.getByLabel("AX 메시지").fill(`둘째 대화 초안 ${stamp}`);
  // History is hidden by default; expand it before switching conversations.
  if ((await page.locator(".ax-conversation-list button").count()) === 0) {
    await page.getByRole("button", { name: "대화 히스토리" }).click();
  }
  await page.locator(`.ax-conversation-list button[data-conversation-id="${first.conversation_id}"]`).click();
  await page.getByLabel("AX 메시지").fill(`첫째 대화 초안 ${stamp}`);

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "AX", exact: true }).click();
  // Reopen the same conversation: each one keeps its own unsent text, not one shared box.
  await page.getByRole("button", { name: "대화 히스토리" }).click();
  const firstButton = page.locator(`.ax-conversation-list button[data-conversation-id="${first.conversation_id}"]`);
  await firstButton.waitFor({ timeout: 20_000 });
  await firstButton.click();
  const restored = await page.getByLabel("AX 메시지").inputValue();
  if (restored !== `첫째 대화 초안 ${stamp}`) throw new Error(`the draft did not survive a reload: ${JSON.stringify(restored)}`);

  await page.screenshot({ path: "test-results/chat-recovery-e2e.png", fullPage: true });
  console.log(JSON.stringify({ result: "a stopped turn kept what it had said, was tried again, and drafts survived", conversation_id: first.conversation_id, stopped }));
} finally {
  await browser.close();
}
