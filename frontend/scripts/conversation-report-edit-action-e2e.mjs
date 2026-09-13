import { chromium } from "@playwright/test";

import { pollFor, loginAs, switchAccount } from "./e2e-helpers.mjs";

const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const reportDate = "2026-09-02";
const title = `AX 보고 편집 근거 ${Date.now()}`;
const editedBody = `AX 승인으로 반영한 보고 편집 ${Date.now()}`;

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
  await page.getByRole("button", { name: "새 업무 추가" }).click();
  await page.getByLabel("업무 제목").fill(title);
  await page.getByRole("button", { name: "업무 추가", exact: true }).click();
  const task = page.locator("tr.progress-row", { hasText: title });
  await task.getByRole("button", { name: "시작" }).click();
  await navigation.getByRole("button", { name: "보고" }).click();
  // 달력 버튼도 같은 말로 이름 붙어 있으므로 필드를 정확히 가리킨다.
  await page.getByLabel("보고일", { exact: true }).fill(reportDate);
  const generatedResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/daily-reports/generate-draft") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "근거로 초안 만들기" }).click();
  const accepted = await (await generatedResponse).json();
  const generated = await pollFor(
    page,
    () => page.evaluate(async ({ reportDate, generationId }) => {
      const statusResponse = await fetch(`/api/daily-reports/status?${new URLSearchParams({ report_date: reportDate })}`, {
        headers: { "X-Demo-Persona": "mina" },
      });
      const status = await statusResponse.json();
      if (status.generation_id !== generationId || status.generation_status !== "completed" || !status.report_id) return null;
      const historyResponse = await fetch(`/api/daily-reports/${status.report_id}/history`, {
        headers: { "X-Demo-Persona": "mina" },
      });
      const history = await historyResponse.json();
      const draft = history.drafts.at(-1);
      return draft ? {
        report_id: status.report_id,
        draft_id: draft.draft_id,
        draft_version: draft.version,
        workflow_run_id: draft.workflow_run_id,
        definition_version_id: draft.definition_version_id,
        status: history.status,
      } : null;
    }, { reportDate, generationId: accepted.generation_id }),
    { timeout: 180_000, description: "the durable daily-report generation" },
  );
  if (
    !generated.workflow_run_id ||
    !generated.definition_version_id ||
    generated.status !== "draft"
  ) {
    throw new Error("Daily-report draft generation did not retain version-pinned workflow provenance");
  }

  await page.getByRole("button", { name: "AX", exact: true }).click();
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  await page.getByLabel("AX 메시지").fill(
    `SCAX MCP의 daily_report_edit를 실제 호출해 report_id ${generated.report_id}, draft_id ${generated.draft_id}, expected_version ${generated.draft_version}, body '${editedBody}'로 초안 수정을 ActionItem으로 제안해. 답변만 하지 말고 도구를 호출하고 화면 승인을 기다려.`,
  );
  const createdResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "보내기" }).click();
  const conversation = await (await createdResponse).json();
  const action = await pollFor(
    page,
    () => page.evaluate(async ({ conversationId, workflowRunId, definitionVersionId }) => {
      const response = await fetch(`/api/conversations/${conversationId}`, { headers: { "X-Demo-Persona": "mina" } });
      const current = await response.json();
      const action = current.actions?.find((item) => item.action_type === "daily_report.edit" && item.state === "pending");
      const tool = current.tool_invocations.find((item) => item.tool_name === "daily_report_edit" && item.state === "completed");
      const reranGeneration = current.tool_invocations.some((item) => item.tool_name === "daily_report_generate_draft");
      return action && tool && workflowRunId && definitionVersionId && !reranGeneration ? action : null;
    }, {
      conversationId: conversation.conversation_id,
      workflowRunId: generated.workflow_run_id,
      definitionVersionId: generated.definition_version_id,
    }),
    { timeout: 120_000, description: "the pending daily-report edit ActionItem" },
  );
  const pendingVersion = action.version;
  await page.locator(`.ax-action-card[data-action-id="${action.action_id}"]`).waitFor();
  await page.getByRole("button", { name: "닫기", exact: true }).click();
  await navigation.getByRole("button", { name: "내 업무" }).click();
  // 판단은 한 곳에서 한다: AX가 준비한 확인도 다른 판단과 같은 카드·같은 상세에서 결정한다.
  const actionCard = page.locator(`.decision-section .task-card[data-action-item-id="${action.action_id}"]`);
  await actionCard.waitFor({ timeout: 20_000 });
  await actionCard.getByRole("button", { name: "판단하기" }).click();
  const decisionDrawer = page.getByRole("dialog", { name: "판단 상세" });
  await decisionDrawer.waitFor({ timeout: 20_000 });
  await decisionDrawer.getByRole("button", { name: "이 내용으로 반영" }).click();
  await page.getByRole("dialog").waitFor({ state: "detached", timeout: 20_000 });
  const history = await pollFor(
    page,
    () => page.evaluate(async ({ actionId, conversationId, reportId, actionVersion, draftVersion, body, workflowRunId, definitionVersionId }) => {
      const headers = { "X-Demo-Persona": "mina" };
      const [actionsResponse, conversationResponse, historyResponse] = await Promise.all([
        fetch("/api/actions", { headers }), fetch(`/api/conversations/${conversationId}`, { headers }), fetch(`/api/daily-reports/${reportId}/history`, { headers }),
      ]);
      const action = (await actionsResponse.json()).find((item) => item.action_id === actionId);
      const conversationAction = (await conversationResponse.json()).actions.find((item) => item.action_id === actionId);
      const history = await historyResponse.json();
      const draft = history.drafts.at(-1);
      const matchingProjection = [action, conversationAction].every((item) =>
        item?.action_id === actionId && item.state === "approved" && item.version === actionVersion + 1 && item.audit_ref &&
        item.audit_ref === action.audit_ref && JSON.stringify(item.result) === JSON.stringify(action.result),
      );
      const matchingResult = action?.result?.report_id === reportId && action.result?.draft_id === draft?.draft_id && action.result?.draft_version === draftVersion + 1;
      const matchingDraft = draft?.version === draftVersion + 1 && draft.body === body && draft.workflow_run_id === workflowRunId && draft.definition_version_id === definitionVersionId;
      return matchingProjection && matchingResult && matchingDraft
        ? { action, draft } : null;
    }, { actionId: action.action_id, conversationId: conversation.conversation_id, reportId: generated.report_id, actionVersion: pendingVersion, draftVersion: generated.draft_version, body: editedBody, workflowRunId: generated.workflow_run_id, definitionVersionId: generated.definition_version_id }),
    { timeout: 30_000, description: "the approved Action audit and incremented report draft" },
  );
  await page.screenshot({ path: "test-results/conversation-report-edit-action-e2e.png", fullPage: true });
  console.log(JSON.stringify({ result: "Codex daily_report_edit proposed and approved", report_date: reportDate, action_id: action.action_id, audit_ref: history.action.audit_ref, draft_version: history.draft.version, workflow_run_id: generated.workflow_run_id, definition_version_id: generated.definition_version_id }));
} finally {
  await browser.close();
}
