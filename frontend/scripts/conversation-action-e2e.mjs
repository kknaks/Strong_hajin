import { chromium } from "@playwright/test";

import { pollFor, loginAs, switchAccount } from "./e2e-helpers.mjs";

const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const requestTitle = `AX 승인 업무 요청 ${Date.now()}`;

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
  await page.getByRole("button", { name: "AX" }).click();

  const createConversationResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  const conversation = await (await createConversationResponse).json();
  await page.getByLabel("AX 메시지").fill(
    [
      "SCAX MCP에서 work_request_assignee_candidates를 먼저 호출한 뒤,",
      `work_request_create로 제목 '${requestTitle}'의 업무 요청을 authorized assignee jiho에게 생성해줘.`,
      "반드시 work_request_create 도구를 실제로 호출해서 ActionItem을 저장해. 답변으로만 제안하지 마.",
      "이 변경은 ActionItem 제안으로 끝내고, 내가 화면에서 승인할 때까지 기다려.",
    ].join(" "),
  );
  await page.getByRole("button", { name: "보내기" }).click();

  const pending = await pollFor(
    page,
    () =>
      page.evaluate(async (conversationId) => {
        const response = await fetch(`/api/conversations/${conversationId}`, {
          headers: { "X-Demo-Persona": "mina" },
        });
        if (!response.ok) return null;
        const current = await response.json();
        const action = current.actions?.find(
          (item) => item.action_type === "work_request.create" && item.state === "pending",
        );
        const tool = current.tool_invocations.find(
          (item) => item.tool_name === "work_request_create" && item.state === "completed",
        );
        if (!action || !tool) return null;
        return action;
      }, conversation.conversation_id),
    { timeout: 120_000, description: "the pending WorkRequest ActionItem and its completed MCP invocation" },
  );
  if (!pending?.action_id || !pending?.version) {
    throw new Error("Codex MCP write did not persist a pending ActionItem");
  }
  const proposedAction = await page.evaluate(async (actionId) => {
    const response = await fetch("/api/actions", { headers: { "X-Demo-Persona": "mina" } });
    const actions = await response.json();
    return actions.find((item) => item.action_id === actionId);
  }, pending.action_id);
  if (proposedAction?.payload_summary !== `업무 요청: ${requestTitle}`) {
    throw new Error("Action proposal did not preserve the requested resource identity");
  }
  const drawerActionCard = page.locator(`.ax-action-card[data-action-id="${pending.action_id}"]`);
  await drawerActionCard.waitFor({ timeout: 20_000 });

  await page.getByRole("button", { name: "닫기", exact: true }).click();
  await navigation.getByRole("button", { name: "내 업무" }).click();
  const actionCard = page.locator(`.decision-panel .task-card[data-action-id="${pending.action_id}"]`);
  await actionCard.waitFor();
  const approvalResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/actions/${pending.action_id}/decide`) && response.request().method() === "POST",
  );
  await actionCard.getByRole("button", { name: "승인" }).click();
  await approvalResponse;

  const approved = await page.evaluate(async ({ actionId, conversationId }) => {
    const headers = { "X-Demo-Persona": "mina" };
    const [actionsResponse, conversationResponse] = await Promise.all([
      fetch("/api/actions", { headers }),
      fetch(`/api/conversations/${conversationId}`, { headers }),
    ]);
    const actions = await actionsResponse.json();
    const current = await conversationResponse.json();
    return {
      action: actions.find((item) => item.action_id === actionId),
      conversationAction: current.actions.find((item) => item.action_id === actionId),
    };
  }, { actionId: pending.action_id, conversationId: conversation.conversation_id });
  if (
    approved.action?.state !== "approved" ||
    !approved.action?.result?.request_id ||
    !approved.action?.audit_ref ||
    approved.conversationAction?.action_id !== pending.action_id ||
    approved.conversationAction?.audit_ref !== approved.action.audit_ref
  ) {
    throw new Error("Action approval did not preserve the canonical Action/audit in both product projections");
  }

  await switchAccount(page, "jiho");
  await navigation.getByRole("button", { name: "오늘" }).click();
  const jihoRequestCard = page.locator(".card-stack .task-card", { hasText: requestTitle });
  await jihoRequestCard.waitFor();
  const inboxRequest = await page.evaluate(async (requestId) => {
    const response = await fetch("/api/action-inbox", {
      headers: { "X-Demo-Persona": "jiho" },
    });
    const requests = await response.json();
    return requests.find((item) => item.request_id === requestId);
  }, approved.action.result.request_id);
  if (inboxRequest?.title !== requestTitle || inboxRequest?.state !== "pending" || inboxRequest.task_id !== null) {
    throw new Error("Approved AX WorkRequest did not reach Jiho's decision inbox without a Task");
  }

  await page.screenshot({ path: "test-results/conversation-action-e2e.png", fullPage: true });
  console.log(
    JSON.stringify({
      result: "Codex MCP write proposed and approved through the canonical ActionItem",
      conversation_id: conversation.conversation_id,
      action_id: approved.action.action_id,
      audit_ref: approved.action.audit_ref,
      work_request_id: approved.action.result.request_id,
    }),
  );
} finally {
  await browser.close();
}
