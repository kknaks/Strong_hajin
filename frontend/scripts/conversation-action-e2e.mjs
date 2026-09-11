import { chromium } from "@playwright/test";

import { pollFor, loginAs, switchAccount } from "./e2e-helpers.mjs";

const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const requestTitle = `AX 승인 업무 요청 ${Date.now()}`;
const referenceTitle = `AX 참고 업무 ${Date.now()}`;

const browser = await chromium.launch({
  executablePath:
    process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");
  const navigation = page.getByRole("navigation", { name: "제품 탐색" });
  const reference = await page.evaluate(async (title) => {
    const response = await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, due_date: "2026-09-20" }),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }, referenceTitle);
  await page.getByRole("button", { name: "AX", exact: true }).click();
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  await page.getByLabel("AX 메시지").fill(
    [
      `graph_search를 먼저 호출해 '${referenceTitle}' 업무를 찾고,`,
      "SCAX MCP에서 work_request_assignee_candidates를 먼저 호출한 뒤,",
      `work_request_create로 제목 '${requestTitle}', 내용 '원안 설명', 기한 2026-09-30, 체크리스트 '수치 검토'의 업무 요청을 authorized assignee jiho에게 생성해줘.`,
      `찾은 '${referenceTitle}' 업무는 reference_task_ids에 연결해.`,
      "반드시 work_request_create 도구를 실제로 호출해서 ActionItem을 저장해. 답변으로만 제안하지 마.",
      "이 변경은 ActionItem 제안으로 끝내고, 내가 화면에서 승인할 때까지 기다려.",
    ].join(" "),
  );
  const createConversationResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "보내기" }).click();
  const conversation = await (await createConversationResponse).json();

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
        const search = current.tool_invocations.find(
          (item) => item.tool_name === "graph_search" && item.state === "completed",
        );
        if (!action || !tool || !search || !action.edit_contract?.values?.reference_task_ids?.length) return null;
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
  if (pending.edit_contract.values.reference_task_ids[0] !== reference.task_id) {
    throw new Error("the provider did not preserve the graph-search Task as the WorkRequest reference");
  }
  const before = await page.evaluate(async (title) => {
    const requests = await (await fetch("/api/work-requests")).json();
    const tasks = await (await fetch("/api/my-work", { headers: { "X-Demo-Persona": "jiho" } })).json();
    return {
      requests: requests.filter((row) => row.title === title).length,
      recipientTasks: tasks.filter((row) => row.title === title).length,
    };
  }, requestTitle);
  if (before.requests !== 0 || before.recipientTasks !== 0) {
    throw new Error(`the proposal mutated the work ledger before confirmation: ${JSON.stringify(before)}`);
  }

  await drawerActionCard.getByRole("button", { name: "수정" }).click();
  await drawerActionCard.getByLabel("내용").fill("사람이 검토한 최종 설명");
  const confirmationResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/action-items/${pending.action_id}/commands/confirm`) && response.request().method() === "POST",
  );
  await drawerActionCard.getByRole("button", { name: "저장" }).click();
  const confirmed = await confirmationResponse;
  if (!confirmed.ok()) throw new Error(await confirmed.text());

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
  if (approved.action.result?.state !== "pending") {
    throw new Error(`confirmation did not create a pending WorkRequest: ${JSON.stringify(approved.action.result)}`);
  }
  const requestDetail = await page.evaluate(async (requestId) => {
    const response = await fetch(`/api/work-requests/${requestId}`);
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }, approved.action.result.request_id);
  if (requestDetail.references?.[0]?.task?.task_id !== reference.task_id) {
    throw new Error("the confirmed WorkRequest did not keep the selected reference Task");
  }

  await switchAccount(page, "jiho");
  await navigation.getByRole("button", { name: "오늘" }).click();
  const jihoRequestCard = page.locator(".card-stack .task-card", { hasText: requestTitle });
  await jihoRequestCard.waitFor();
  const judgement = await page.evaluate(async (requestId) => {
    const items = await (await fetch("/api/action-items", { headers: { "X-Demo-Persona": "jiho" } })).json();
    return items.find((item) => item.resource.type === "work_request" && item.resource.id === requestId);
  }, approved.action.result.request_id);
  if (judgement?.subject !== requestTitle || judgement?.status !== "awaiting_review" || judgement?.kind !== "work_request.acceptance") {
    throw new Error(`Approved AX WorkRequest did not become Jiho's judgement without a Task: ${JSON.stringify(judgement)}`);
  }

  await navigation.getByRole("button", { name: "내 업무" }).click();
  await page.getByRole("tab", { name: "할일" }).click();
  const judgementCard = page.locator(`.task-card[data-action-item-id="${judgement.action_item_id}"]`);
  await judgementCard.getByRole("button", { name: "판단하기" }).click();
  const judgementDrawer = page.getByRole("dialog", { name: "판단 상세" });
  await judgementDrawer.getByRole("button", { name: "수락" }).click();
  await judgementDrawer.waitFor({ state: "detached", timeout: 20_000 });
  const accepted = await pollFor(
    page,
    () => page.evaluate(async (title) => {
      const tasks = await (await fetch("/api/my-work")).json();
      const task = tasks.find((row) => row.title === title);
      if (!task) return null;
      const detail = await (await fetch(`/api/tasks/${task.task_id}`)).json();
      return detail.references?.length === 1
        && detail.checklist?.some((row) => row.text === "수치 검토")
        ? detail
        : null;
    }, requestTitle),
    { timeout: 20_000, description: "the accepted Task with its selected checklist and reference" },
  );

  await page.screenshot({ path: "test-results/conversation-action-e2e.png", fullPage: true });
  console.log(
    JSON.stringify({
      result: "Codex MCP write proposed and approved through the canonical ActionItem",
      conversation_id: conversation.conversation_id,
      action_id: approved.action.action_id,
      audit_ref: approved.action.audit_ref,
      work_request_id: approved.action.result.request_id,
      task_id: accepted.task_id,
      reference_task_id: reference.task_id,
    }),
  );
} finally {
  await browser.close();
}
