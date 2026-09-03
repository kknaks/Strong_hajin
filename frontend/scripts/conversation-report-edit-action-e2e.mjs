import { chromium } from "@playwright/test";

import { pollFor } from "./e2e-helpers.mjs";

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
  const navigation = page.getByRole("navigation", { name: "제품 탐색" });
  await navigation.getByRole("button", { name: "내 업무" }).click();
  await page.getByLabel("업무 제목").fill(title);
  await page.getByRole("button", { name: "업무 추가" }).click();
  const task = page.locator("article.progress-row", { hasText: title });
  await task.getByRole("button", { name: "시작" }).click();
  await navigation.getByRole("button", { name: "보고" }).click();
  await page.getByLabel("보고일").fill(reportDate);
  const generatedResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/daily-reports/generate-draft") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "근거로 초안 만들기" }).click();
  const generated = await (await generatedResponse).json();
  if (
    !generated.workflow_run_id ||
    !generated.definition_version_id ||
    generated.status !== "draft"
  ) {
    throw new Error("Daily-report draft generation did not retain version-pinned workflow provenance");
  }

  await page.getByRole("button", { name: "AX" }).click();
  const createdResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  const conversation = await (await createdResponse).json();
  await page.getByLabel("AX 메시지").fill(
    `SCAX MCP의 daily_report_edit를 실제 호출해 report_id ${generated.report_id}, draft_id ${generated.draft_id}, expected_version ${generated.draft_version}, body '${editedBody}'로 초안 수정을 ActionItem으로 제안해. 답변만 하지 말고 도구를 호출하고 화면 승인을 기다려.`,
  );
  await page.getByRole("button", { name: "보내기" }).click();
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
  await page.getByRole("button", { name: "닫기" }).click();
  await navigation.getByRole("button", { name: "판단" }).click();
  const actionCard = page.locator(`li[data-action-id="${action.action_id}"]`);
  await actionCard.getByRole("button", { name: "승인" }).click();
  const history = await pollFor(
    page,
    () => page.evaluate(async ({ actionId, conversationId, reportId, version, body, workflowRunId, definitionVersionId }) => {
      const headers = { "X-Demo-Persona": "mina" };
      const [actionsResponse, conversationResponse, historyResponse] = await Promise.all([
        fetch("/api/actions", { headers }), fetch(`/api/conversations/${conversationId}`, { headers }), fetch(`/api/daily-reports/${reportId}/history`, { headers }),
      ]);
      const action = (await actionsResponse.json()).find((item) => item.action_id === actionId);
      const conversationAction = (await conversationResponse.json()).actions.find((item) => item.action_id === actionId);
      const history = await historyResponse.json();
      const draft = history.drafts.at(-1);
      const matchingProjection = [action, conversationAction].every((item) =>
        item?.action_id === actionId && item.state === "approved" && item.version === version + 1 && item.audit_ref &&
        item.audit_ref === action.audit_ref && JSON.stringify(item.result) === JSON.stringify(action.result),
      );
      const matchingResult = action?.result?.report_id === reportId && action.result?.draft_id === draft?.draft_id && action.result?.draft_version === version + 1;
      const matchingDraft = draft?.version === version + 1 && draft.body === body && draft.workflow_run_id === workflowRunId && draft.definition_version_id === definitionVersionId;
      return matchingProjection && matchingResult && matchingDraft
        ? { action, draft } : null;
    }, { actionId: action.action_id, conversationId: conversation.conversation_id, reportId: generated.report_id, version: pendingVersion, body: editedBody, workflowRunId: generated.workflow_run_id, definitionVersionId: generated.definition_version_id }),
    { timeout: 30_000, description: "the approved Action audit and incremented report draft" },
  );
  await page.screenshot({ path: "test-results/conversation-report-edit-action-e2e.png", fullPage: true });
  console.log(JSON.stringify({ result: "Codex daily_report_edit proposed and approved", report_date: reportDate, action_id: action.action_id, audit_ref: history.action.audit_ref, draft_version: history.draft.version, workflow_run_id: generated.workflow_run_id, definition_version_id: generated.definition_version_id }));
} finally {
  await browser.close();
}
