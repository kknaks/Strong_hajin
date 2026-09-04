import { chromium } from "@playwright/test";

import { pollFor, loginAs, switchAccount } from "./e2e-helpers.mjs";

const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";

const browser = await chromium.launch({
  executablePath:
    process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");
  await page.getByRole("button", { name: "AX" }).click();
  const newConversation = page.getByRole("button", { name: "새 AX 대화" });
  const createFirstConversation = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await newConversation.click();
  const firstConversation = await (await createFirstConversation).json();
  await page
    .getByLabel("AX 메시지")
    .fill("SCAX MCP의 task_list를 사용해 첫 번째 대화의 내 업무 수만 알려줘.");
  await page.getByRole("button", { name: "보내기" }).click();
  await page.getByRole("button", { name: "대기열에 보내기" }).waitFor({ timeout: 20_000 });
  await page
    .getByLabel("AX 메시지")
    .fill("첫 번째 대화의 두 번째 발화입니다. 같은 task_list를 다시 확인해줘.");
  await page.getByRole("button", { name: "대기열에 보내기" }).click();
  await page.getByText("대기 중").waitFor({ timeout: 20_000 });

  const createSecondConversation = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await newConversation.click();
  const secondConversation = await (await createSecondConversation).json();
  await page
    .getByLabel("AX 메시지")
    .fill("SCAX MCP의 task_list를 사용해 두 번째 대화의 내 업무 수만 알려줘.");
  await page.getByRole("button", { name: "보내기" }).click();

  const conversationButton = (conversationId) =>
    page.locator(`.ax-conversation-list button[data-conversation-id="${conversationId}"]`);
  const activeTimeline = page.locator(".ax-messages");
  await conversationButton(firstConversation.conversation_id).click();
  await activeTimeline.getByText("첫 번째 대화의 두 번째 발화입니다.").waitFor({ timeout: 20_000 });
  await activeTimeline.getByText("두 번째 대화의 내 업무 수만 알려줘.").count().then((count) => {
    if (count !== 0) throw new Error("Conversation state leaked across the active-session switch");
  });
  await conversationButton(firstConversation.conversation_id).click();
  const toolSummary = page.locator(".ax-rail.terminal .ax-rail-summary", { hasText: /✓ 완료 · 도구 \d+개/ }).first();
  await toolSummary.waitFor({ timeout: 90_000 });
  await toolSummary.click();
  await page.locator(".ax-rail-tool.completed", { hasText: "task list" }).first().waitFor();
  await conversationButton(secondConversation.conversation_id).click();
  await pollFor(
    page,
    () =>
      page.evaluate(async () => {
        const headers = { "X-Demo-Persona": "mina" };
        const response = await fetch("/api/conversations", { headers });
        const items = await response.json();
        const first = items.find((conversation) =>
          conversation.messages.some(
            (message) => message.body === "첫 번째 대화의 두 번째 발화입니다. 같은 task_list를 다시 확인해줘.",
          ),
        );
        if (!first) return null;
        const firstMessage = first.messages.find(
          (message) => message.body === "SCAX MCP의 task_list를 사용해 첫 번째 대화의 내 업무 수만 알려줘.",
        );
        const queuedMessage = first.messages.find(
          (message) => message.body === "첫 번째 대화의 두 번째 발화입니다. 같은 task_list를 다시 확인해줘.",
        );
        if (!firstMessage?.turn_id || !queuedMessage?.turn_id || firstMessage.turn_id === queuedMessage.turn_id) {
          return null;
        }
        const completedTurnIds = first.turns
          .filter((turn) => turn.state === "completed")
          .map((turn) => turn.turn_id);
        if (
          completedTurnIds.length < 2 ||
          !completedTurnIds.includes(firstMessage.turn_id) ||
          !completedTurnIds.includes(queuedMessage.turn_id)
        ) {
          return null;
        }
        return [firstMessage.turn_id, queuedMessage.turn_id].every((turnId) =>
          first.tool_invocations.some(
            (tool) => tool.turn_id === turnId && tool.tool_name === "task_list" && tool.state === "completed",
          ),
        );
      }),
    { timeout: 90_000, description: "both queued fragments completing their own task_list turn" },
  );
  await page.screenshot({ path: "test-results/conversation-e2e.png", fullPage: true });
  console.log(
    JSON.stringify({
      result: "persona-bound task_list completed with queued follow-up and independent conversation",
    }),
  );
} finally {
  await browser.close();
}
