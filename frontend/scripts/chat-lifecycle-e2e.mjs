import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

// Phase 1.1 chat lifecycle against the real local stack: live progress states (tool_running observed before the
// turn completes), per-tool observed timing, the collapsed one-line rail after the final answer, and a projection
// that survives switching conversations and re-entering (no client-only state).
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";

const browser = await chromium.launch({
  executablePath:
    process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

const seenStates = new Set();

try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");
  await page.getByRole("button", { name: "AX" }).click();
  const newConversation = page.getByRole("button", { name: "새 AX 대화" });
  const createFirst = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await newConversation.click();
  const first = await (await createFirst).json();
  const prompt = "SCAX MCP의 task_list를 사용해 내 업무 수만 알려줘.";
  await page.getByLabel("AX 메시지").fill(prompt);
  await page.getByRole("button", { name: "보내기" }).click();

  // The optimistic row shows at once and converges onto the server row without a duplicate.
  await page.locator(".ax-messages").getByText(prompt).first().waitFor({ timeout: 10_000 });
  const rail = page.locator(".ax-rail").first();
  await rail.waitFor({ timeout: 20_000 });

  // Observe user-facing progress states as the worker ingests Codex JSONL. A tool receipt must be visible in the rail
  // while the turn is still non-terminal (live tool display before the final answer). `tool_running` itself lasts only
  // as long as the tool call (task_list takes tens of milliseconds), so it is recorded when seen but not required.
  const liveReceipt = await pollFor(
    page,
    async () => {
      const state = await rail.getAttribute("data-progress");
      if (state) seenStates.add(state);
      const terminal = state === "completed" || state === "failed" || state === "cancelled";
      const receipts = page.locator(".ax-rail .ax-rail-tool");
      if (!terminal && (await receipts.count()) > 0) {
        const name = ((await receipts.first().locator(".ax-rail-tool-name").textContent()) ?? "").trim();
        const toolState = ((await receipts.first().locator(".ax-rail-tool-state").textContent()) ?? "").trim();
        if (name) return { name, toolState, turnState: state };
      }
      if (terminal) {
        throw new Error(`turn reached ${state} before a live tool receipt was observed (states seen: ${[...seenStates].join(",")})`);
      }
      return null;
    },
    { timeout: 90_000, description: "a live tool receipt while the turn is still running" },
  );
  const duplicates = await page.locator(".ax-messages").getByText(prompt).count();
  if (duplicates !== 1) throw new Error(`user request rendered ${duplicates} times while running (expected 1)`);

  // Final answer: rail collapses to one line with observed timings; the assistant body is the primary content.
  const summary = page.locator(".ax-rail.terminal .ax-rail-summary", { hasText: /✓ 완료 · 도구 \d+개/ }).first();
  await summary.waitFor({ timeout: 90_000 });
  const timings = (await page.locator(".ax-rail.terminal .ax-rail-timings").first().textContent()) ?? "";
  if (!/실행 \d+(\.\d+)?s/.test(timings) || !/대기 /.test(timings)) {
    throw new Error(`terminal rail is missing observed timings: ${JSON.stringify(timings)}`);
  }
  if ((await page.locator(".ax-rail.terminal details[open]").count()) !== 0) throw new Error("terminal rail did not collapse");
  const answer = page.locator(".ax-messages .assistant[data-body-state='final']").first();
  await answer.waitFor({ timeout: 10_000 });
  const answerText = ((await answer.textContent()) ?? "").trim();
  if (!answerText) throw new Error("final assistant body is empty");
  await summary.click();
  const receipt = page.locator(".ax-rail-tool.completed", { hasText: "task list" }).first();
  await receipt.waitFor();
  const toolTime = (await receipt.locator(".ax-rail-tool-time").textContent().catch(() => "")) ?? "";
  if (!/\d/.test(toolTime)) throw new Error(`completed tool receipt has no observed duration: ${JSON.stringify(toolTime)}`);

  // Server projection carries the same lifecycle facts (not client-only state).
  const projection = await page.evaluate(async (conversationId) => {
    const response = await fetch(`/api/conversations/${conversationId}`, { headers: { "X-Demo-Persona": "mina" } });
    return response.json();
  }, first.conversation_id);
  const turn = projection.turns[0];
  const assistant = projection.messages.find((message) => message.role === "assistant");
  if (turn.progress_state !== "completed" || turn.run_ms == null || turn.queue_wait_ms == null || turn.execution_started_at == null) {
    throw new Error(`projection lacks lifecycle facts: ${JSON.stringify(turn)}`);
  }
  // The body is rendered as Markdown: compare the text content with the emphasis/code markers removed and make sure
  // no raw Markdown syntax leaks into the rendered answer.
  const plain = (text) => text.replace(/[*_`\s]/g, "");
  if (!assistant || assistant.body_state !== "final" || plain(assistant.body) !== plain(answerText)) {
    throw new Error(`assistant body in the projection does not match the rendered final answer: ${JSON.stringify({ projected: assistant?.body, rendered: answerText })}`);
  }
  if (/\*\*/.test(answerText)) throw new Error(`raw Markdown emphasis leaked into the rendered answer: ${JSON.stringify(answerText)}`);
  if (/\*\*/.test(assistant.body) && (await answer.locator("strong").count()) === 0) {
    throw new Error("projected emphasis was not rendered as <strong>");
  }
  const tool = projection.tool_invocations.find((item) => item.turn_id === turn.turn_id && item.tool_name === "task_list");
  if (!tool || tool.state !== "completed" || tool.started_at == null || tool.completed_at == null || tool.latency_ms == null) {
    throw new Error(`task_list tool row lacks observed timing: ${JSON.stringify(tool)}`);
  }

  // Switch away and back: the answer, collapsed rail and timings come from the projection, not from local state.
  const createSecond = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await newConversation.click();
  const second = await (await createSecond).json();
  const conversationButton = (conversationId) =>
    page.locator(`.ax-conversation-list button[data-conversation-id="${conversationId}"]`);
  await conversationButton(second.conversation_id).waitFor();
  if ((await page.locator(".ax-messages").getByText(prompt).count()) !== 0) throw new Error("first conversation leaked into the new one");
  await page.reload({ waitUntil: "domcontentloaded" });
  // The session survives the reload; log in again only if the login screen is shown.
  await Promise.race([
    page.getByRole("navigation", { name: "제품 탐색" }).waitFor(),
    page.getByRole("radio", { name: /^민아/ }).waitFor(),
  ]);
  if ((await page.getByRole("radio", { name: /^민아/ }).count()) > 0) await loginAs(page, "mina");
  await page.getByRole("button", { name: "AX" }).click();
  await conversationButton(first.conversation_id).click();
  await page.locator(".ax-messages").getByText(prompt).first().waitFor({ timeout: 10_000 });
  await page.locator(".ax-rail.terminal .ax-rail-summary", { hasText: /✓ 완료 · 도구 \d+개/ }).first().waitFor({ timeout: 10_000 });
  const restored = ((await page.locator(".ax-messages .assistant[data-body-state='final']").first().textContent()) ?? "").trim();
  if (restored !== answerText) throw new Error("final answer did not survive re-entry from the projection");

  await page.screenshot({ path: "test-results/chat-lifecycle-e2e.png", fullPage: true });
  console.log(
    JSON.stringify({
      result: "chat lifecycle projected live and restored on re-entry",
      states_observed: [...seenStates],
      live_receipt: liveReceipt,
      run_ms: turn.run_ms,
      queue_wait_ms: turn.queue_wait_ms,
      tool_latency_ms: tool.latency_ms,
    }),
  );
} finally {
  await browser.close();
}
