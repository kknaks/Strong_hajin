import { chromium } from "@playwright/test";

import { loginAs, pollFor, switchAccount } from "./e2e-helpers.mjs";

const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const title = `Playwright 보고 근거 업무 ${Date.now()}`;
const editedBody = `사람이 확인한 Playwright 일일보고 ${Date.now()}`;

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
  const createTaskResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/tasks") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "새 업무 추가" }).click();
  await page.getByLabel("업무 제목").fill(title);
  await page.getByRole("button", { name: "업무 추가", exact: true }).click();
  const createdTask = await (await createTaskResponse).json();
  const taskRow = page.locator("tr.progress-row", { hasText: title });
  await taskRow.getByRole("button", { name: "시작" }).click();
  await taskRow.getByText("진행 중", { exact: true }).waitFor();

  await navigation.getByRole("button", { name: "보고" }).click();
  await page.getByRole("button", { name: "근거로 초안 만들기" }).waitFor();
  const generateResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/daily-reports/generate-draft") && response.request().method() === "POST",
    { timeout: 180_000 },
  );
  await page.getByRole("button", { name: "근거로 초안 만들기" }).click();
  const accepted = await (await generateResponse).json();
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
        body: draft.body,
        source_refs: draft.source_refs,
        workflow_run_id: draft.workflow_run_id,
        definition_version_id: draft.definition_version_id,
        workflow_state: "completed",
        status: history.status,
      } : null;
    }, { reportDate: accepted.report_date, generationId: accepted.generation_id }),
    { timeout: 180_000, description: "the durable daily-report generation" },
  );
  if (
    generated.status !== "draft" ||
    generated.workflow_state !== "completed" ||
    !generated.workflow_run_id ||
    !generated.definition_version_id ||
    !generated.source_refs.some(
      (source) => source.task_id === createdTask.task_id && source.task_version === 2 && source.state === "in_progress",
    )
  ) {
    throw new Error("Daily-report generation did not return the version-pinned production runtime result");
  }

  await navigation.getByRole("button", { name: "오늘" }).click();
  await navigation.getByRole("button", { name: "보고" }).click();
  const draftEditor = page.getByLabel("일일보고 초안");
  await page.getByText(`초안 v${generated.draft_version}`, { exact: true }).waitFor({ timeout: 30_000 });
  await pollFor(
    page,
    () => draftEditor.inputValue().then((body) => (body === generated.body ? body : null)),
    { timeout: 30_000, description: "the generated daily-report draft in the editor" },
  );
  await draftEditor.fill(editedBody);
  const editResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/daily-reports/${generated.report_id}/edit`) && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "편집 저장" }).click();
  const editNetworkResponse = await editResponse;
  const editRequest = editNetworkResponse.request().postDataJSON();
  const edited = await editNetworkResponse.json();
  if (
    editRequest.draft_id !== generated.draft_id ||
    editRequest.expected_version !== generated.draft_version ||
    edited.draft_version <= generated.draft_version ||
    edited.body !== editedBody
  ) {
    throw new Error("Daily-report edit did not use the generated draft version");
  }

  const submitResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/daily-reports/${generated.report_id}/submit`) && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "보고 제출" }).click();
  const submitNetworkResponse = await submitResponse;
  const submitRequest = submitNetworkResponse.request().postDataJSON();
  const submitted = await submitNetworkResponse.json();
  if (submitRequest.expected_version !== edited.draft_version || submitted.submission_version !== 1) {
    throw new Error("Daily-report submission did not use the edited draft version");
  }
  await page.getByText("제출 v1").waitFor();

  await navigation.getByRole("button", { name: "오늘" }).click();
  await navigation.getByRole("button", { name: "보고" }).click();
  const restoredDraft = page.getByLabel("일일보고 초안");
  await restoredDraft.waitFor();
  if ((await restoredDraft.inputValue()) !== editedBody) {
    throw new Error("Daily-report draft was not restored after re-entering the report page");
  }
  await page.getByText("제출 v1").waitFor();
  const history = await page.evaluate(async (reportId) => {
    const response = await fetch(`/api/daily-reports/${reportId}/history`, {
      headers: { "X-Demo-Persona": "mina" },
    });
    return response.json();
  }, generated.report_id);
  if (
    history.submissions.length !== 1 ||
    history.submissions[0].body !== editedBody ||
    history.submissions[0].source_refs.length === 0
  ) {
    throw new Error("Daily-report immutable submission was not restored from history");
  }

  await page.screenshot({ path: "test-results/daily-report-e2e.png", fullPage: true });
  console.log(
    JSON.stringify({
      result: "daily report created, edited, submitted, and restored through the browser",
      report_id: generated.report_id,
      draft_id: edited.draft_id,
      workflow_run_id: generated.workflow_run_id,
      definition_version_id: generated.definition_version_id,
      submission_version: submitted.submission_version,
    }),
  );
} finally {
  await browser.close();
}
