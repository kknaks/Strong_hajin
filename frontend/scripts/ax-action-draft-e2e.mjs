import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const storageKey = "scax.ax.action-drafts.v1";
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

async function createEditableProposal(page, title, editedTitle) {
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  await page.getByLabel("AX 메시지").fill(
    `SCAX MCP의 task_create_self 도구를 실제로 호출해 제목 '${title}'인 내 업무 생성 제안을 만들고, 카드에서 확정할 때까지 기다려.`,
  );
  const created = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "보내기" }).click();
  const conversation = await (await created).json();
  const action = await pollFor(
    page,
    () => page.evaluate(async ({ conversationId, wantedTitle }) => {
      const response = await fetch(`/api/conversations/${conversationId}`);
      if (!response.ok) return null;
      const current = await response.json();
      return current.actions?.find(
        (candidate) => candidate.action_type === "task.create_self"
          && candidate.state === "pending"
          && candidate.subject === wantedTitle,
      ) ?? null;
    }, { conversationId: conversation.conversation_id, wantedTitle: title }),
    { timeout: 120_000, description: `editable Action for ${title}` },
  );
  const card = page.locator(`.ax-action-card[data-action-id="${action.action_id}"]`);
  await card.waitFor({ timeout: 20_000 });
  await pollFor(
    page,
    () => page.evaluate(async ({ conversationId, turnId }) => {
      const current = await (await fetch(`/api/conversations/${conversationId}`)).json();
      return current.messages?.some((message) =>
        message.turn_id === turnId && message.role === "assistant" && message.body_state === "final",
      );
    }, { conversationId: conversation.conversation_id, turnId: action.turn_id }),
    { timeout: 30_000, description: `the answer for ${title} to enter conversation history` },
  );
  await card.getByRole("button", { name: "수정" }).click();
  await card.getByLabel("업무 명").fill(editedTitle);
  return { conversation, action, editedTitle };
}

async function selectConversation(page, conversationId) {
  await page.getByRole("button", { name: "대화 히스토리" }).click();
  await page.locator(`[data-conversation-id="${conversationId}"]`).click();
}

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "jiho");
  await page.evaluate((key) => window.localStorage.removeItem(key), storageKey);
  await page.getByRole("button", { name: "AX", exact: true }).click();

  const first = await createEditableProposal(page, `복구 원안 A ${stamp}`, `복구 초안 A ${stamp}`);
  const second = await createEditableProposal(page, `복구 원안 B ${stamp}`, `복구 초안 B ${stamp}`);

  await selectConversation(page, first.conversation.conversation_id);
  const firstCard = page.locator(`.ax-action-card[data-action-id="${first.action.action_id}"]`);
  await firstCard.getByLabel("업무 명").waitFor();
  if (await firstCard.getByLabel("업무 명").inputValue() !== first.editedTitle) {
    throw new Error("switching conversations did not restore the first Action draft");
  }

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("navigation", { name: "제품 탐색" }).waitFor();
  await page.getByRole("button", { name: "AX", exact: true }).click();
  await selectConversation(page, first.conversation.conversation_id);
  await firstCard.getByLabel("업무 명").waitFor({ timeout: 20_000 });
  if (await firstCard.getByLabel("업무 명").inputValue() !== first.editedTitle) {
    throw new Error("reloading the browser did not restore the first Action draft");
  }

  const beforeReset = await page.evaluate((key) => Object.values(JSON.parse(window.localStorage.getItem(key) ?? "{}")), storageKey);
  if (beforeReset.length !== 2 || beforeReset.some((record) => record.principal_id !== "jiho" || record.base_submission_version !== 1)) {
    throw new Error(`draft identities were not isolated by principal/action/base: ${JSON.stringify(beforeReset)}`);
  }
  await firstCard.getByRole("button", { name: "초기화" }).click();

  await selectConversation(page, second.conversation.conversation_id);
  const secondCard = page.locator(`.ax-action-card[data-action-id="${second.action.action_id}"]`);
  await secondCard.getByLabel("업무 명").waitFor();
  if (await secondCard.getByLabel("업무 명").inputValue() !== second.editedTitle) {
    throw new Error("resetting one Action erased another conversation's draft");
  }
  const afterReset = await page.evaluate((key) => Object.values(JSON.parse(window.localStorage.getItem(key) ?? "{}")), storageKey);
  if (afterReset.length !== 1 || afterReset[0].action_item_id !== second.action.action_id) {
    throw new Error(`reset did not clean only the selected Action: ${JSON.stringify(afterReset)}`);
  }
  await page.screenshot({ path: "test-results/ax-action-draft-recovery-e2e.png", fullPage: true });
  await secondCard.getByRole("button", { name: "초기화" }).click();

  // A draft tied to an older Submission is displayed as a conflict. It is never silently merged into base 1.
  await page.evaluate(({ key, actionId, draft }) => {
    const id = ["jiho", actionId, "0"].map(encodeURIComponent).join("::");
    window.localStorage.setItem(key, JSON.stringify({
      [id]: {
        principal_id: "jiho",
        action_item_id: actionId,
        base_submission_version: 0,
        draft: { ...draft, title: `오래된 ${draft.title}` },
      },
    }));
  }, { key: storageKey, actionId: second.action.action_id, draft: second.action.edit_contract.values });
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("navigation", { name: "제품 탐색" }).waitFor();
  await page.getByRole("button", { name: "AX", exact: true }).click();
  await selectConversation(page, second.conversation.conversation_id);
  const stale = page.getByRole("status", { name: "저장 초안 충돌" });
  await stale.waitFor({ timeout: 20_000 });
  await stale.scrollIntoViewIfNeeded();
  if (!(await stale.textContent()).includes(`최신안: ${second.action.subject}`)) {
    throw new Error(`stale recovery did not show the latest Submission: ${await stale.textContent()}`);
  }
  await page.screenshot({ path: "test-results/ax-action-draft-stale-e2e.png", fullPage: true });
  await stale.getByRole("button", { name: "최신안으로 다시 시작" }).click();
  if (await page.getByLabel("업무 명").inputValue() !== second.action.subject) {
    throw new Error("choosing the latest Submission did not discard the stale local draft");
  }

  console.log(JSON.stringify({
    result: "two Action drafts survived switching/reload, and stale data required an explicit choice",
    action_ids: [first.action.action_id, second.action.action_id],
  }));
} finally {
  await browser.close();
}
