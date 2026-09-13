import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

// AX proposal → typed Meeting editor → one local Meeting plus its frozen initial note → navigable receipt.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const pastTitle = `지난 출시 점검 ${stamp}`;
const basisTaskTitle = `출시 범위 확인 ${stamp}`;
const materialName = `출시 결정 근거 ${stamp}.txt`;
const materialToken = `모바일 알림은 후속 배포로 분리 ${stamp}`;
const originalTitle = `AX 회의 원안 ${stamp}`;
const finalTitle = `AX 회의 확정 ${stamp}`;
const linkName = `회의 사전 자료 ${stamp}`;
const linkUrl = `https://example.com/meeting-notes/${stamp}`;

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");

  const basis = await page.evaluate(async ({ pastTitle, basisTaskTitle, materialName, materialToken }) => {
    const taskResponse = await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: basisTaskTitle,
        description: "지난 회의 결정에 따라 현재 상태를 확인할 업무",
        due_date: "2026-09-19",
        checklist: ["모바일 알림 제외 범위 확인"],
      }),
    });
    if (!taskResponse.ok) throw new Error(await taskResponse.text());
    const task = await taskResponse.json();
    const meetingResponse = await fetch("/api/meetings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        organization_id: "scax",
        title: pastTitle,
        description: "출시 범위를 결정한 과거 회의",
        starts_at: "2026-09-10T01:00:00Z",
        ends_at: "2026-09-10T02:00:00Z",
        visibility: "private",
        attendee_ids: ["jiho"],
      }),
    });
    if (!meetingResponse.ok) throw new Error(await meetingResponse.text());
    const meeting = await meetingResponse.json();
    const noteResponse = await fetch(`/api/meetings/${meeting.meeting_id}/note`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body: `결정: 모바일 알림은 후속 배포로 분리한다. 연결 업무: ${basisTaskTitle}` }),
    });
    if (!noteResponse.ok) throw new Error(await noteResponse.text());
    const refreshed = await fetch(`/api/meetings/${meeting.meeting_id}`).then((response) => response.json());
    const form = new FormData();
    form.append("expected_version", String(refreshed.version));
    form.append("file", new File([`출시 결정 근거: ${materialToken}`], materialName, { type: "text/plain" }));
    const materialResponse = await fetch(`/api/meetings/${meeting.meeting_id}/materials`, {
      method: "POST",
      body: form,
    });
    if (!materialResponse.ok) throw new Error(await materialResponse.text());
    const material = await materialResponse.json();
    const finalMeeting = await fetch(`/api/meetings/${meeting.meeting_id}`).then((response) => response.json());
    return { meeting: finalMeeting, task, material };
  }, { pastTitle, basisTaskTitle, materialName, materialToken });

  await page.getByRole("button", { name: "AX", exact: true }).click();
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  await page.getByLabel("AX 메시지").fill(
    [
      `'${pastTitle}' 회의의 결정과 회의록을 찾아 상세히 확인해줘.`,
      `회의록에 적힌 '${basisTaskTitle}' 업무의 현재 상태와 첨부 자료에 적힌 '${materialToken}' 내용도 확인해.`,
      `그 근거를 바탕으로 2026년 9월 20일 오전 10시부터 11시까지 '${originalTitle}' 후속 회의를 제안해줘.`,
      "참석자는 지호이고, 내용은 '출시 범위와 담당자별 막힌 점을 확인합니다.'로 해줘.",
      `확인한 '${basisTaskTitle}' 업무는 참고 업무로 연결하고, 초기 회의록에는 '출시 범위와 담당자별 막힌 점을 확인한다.'를 넣어줘.`,
      "초대나 알림, 녹음은 하지 말고 내가 카드에서 수정하고 확정할 때까지 기다려.",
    ].join(" "),
  );
  const createConversation = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "보내기" }).click();
  const conversation = await (await createConversation).json();

  const pending = await pollFor(
    page,
    () => page.evaluate(async ({ conversationId, title }) => {
      const response = await fetch(`/api/conversations/${conversationId}`);
      if (!response.ok) return null;
      const current = await response.json();
      const action = current.actions?.find(
        (item) => item.action_type === "meeting.reservation.create" && item.state === "pending" && item.subject === title,
      );
      const meetingRead = current.tool_invocations?.find(
        (item) => item.tool_name === "meeting_get" && item.state === "completed",
      );
      const taskRead = current.tool_invocations?.find(
        (item) => item.tool_name === "task_get" && item.state === "completed",
      );
      const materialRead = current.tool_invocations?.find(
        (item) => item.tool_name === "material_search" && item.state === "completed",
      );
      return action && meetingRead && taskRead && materialRead ? action : null;
    }, { conversationId: conversation.conversation_id, title: originalTitle }),
    { timeout: 180_000, description: "the source-populated editable meeting reservation Action" },
  );
  const fieldIds = pending.edit_contract?.fields?.map((field) => field.id);
  for (const required of ["organization_id", "title", "description", "starts_at", "ends_at", "visibility", "host_id", "attendee_ids", "reference_task_ids", "include_initial_note", "initial_note_body"]) {
    if (!fieldIds?.includes(required)) throw new Error(`server Meeting contract lacks ${required}: ${JSON.stringify(fieldIds)}`);
  }
  if (pending.edit_contract.values.initial_note_source_status !== "resolved") {
    throw new Error(`initial note did not classify the observed evidence: ${JSON.stringify(pending.edit_contract.values)}`);
  }
  const evidence = pending.edit_contract.values.initial_note_source_evidence ?? [];
  const pastMeetingEvidence = evidence.find(
    (source) => source.source_type === "meeting" && source.source_id === basis.meeting.meeting_id,
  );
  if (
    !pastMeetingEvidence
    || pastMeetingEvidence.locator?.resource_version !== basis.meeting.version
    || !evidence.some((source) => source.source_type === "task" && source.source_id === basis.task.task_id)
    || !evidence.some((source) => source.source_type === "material")
  ) {
    throw new Error(`the proposal did not freeze the Meeting, Task, and material evidence: ${JSON.stringify(evidence)}`);
  }

  const card = page.locator(`.ax-action-card[data-action-id="${pending.action_id}"]`);
  await card.waitFor({ timeout: 20_000 });
  await card.getByText("새 회의록", { exact: true }).waitFor();
  await card.getByRole("button", { name: "업무나 자료 첨부" }).click();
  let picker = page.getByRole("dialog", { name: "업무나 자료 첨부" });
  await picker.getByText("찾고 싶은 업무나 자료를 검색하세요").waitFor();
  await picker.getByRole("tab", { name: "링크 추가" }).click();
  await picker.getByLabel("링크 주소").fill(linkUrl);
  await picker.getByLabel("링크 이름").fill(linkName);
  await picker.getByRole("button", { name: "링크 추가" }).click();
  await card.getByRole("button", { name: "취소" }).click();
  await card.getByText(linkName, { exact: true }).waitFor();
  await card.getByRole("button", { name: `${linkName} 첨부 제외` }).waitFor();
  await card.getByRole("button", { name: "새 회의록 제외" }).waitFor();
  await page.screenshot({ path: "test-results/ax-editable-meeting-summary-e2e.png", fullPage: true });
  await card.screenshot({ path: "test-results/ax-meeting-with-attachment-note-summary-e2e.png" });
  await card.getByRole("button", { name: "수정" }).click();
  const title = card.getByLabel("회의 명");
  await title.waitFor();
  if (!(await title.evaluate((element) => element === document.activeElement))) {
    throw new Error("Meeting editor did not focus its first editable field");
  }
  await card.getByRole("button", { name: "업무나 자료 첨부" }).click();
  picker = page.getByRole("dialog", { name: "업무나 자료 첨부" });
  await picker.getByLabel("업무나 자료 검색").fill(basis.task.title);
  await picker.getByRole("button", { name: new RegExp(basis.task.title) }).click();
  await picker.getByText("선택한 항목 1개", { exact: true }).waitFor();
  await picker.getByRole("button", { name: "첨부", exact: true }).click();
  await card.getByText(basis.task.title, { exact: true }).waitFor();
  await title.fill(finalTitle);
  if (await card.getByLabel("조직").count() || await card.getByLabel("공개 범위").count()) {
    throw new Error("Figma Meeting editor exposed implementation-only organization or visibility controls");
  }
  await card.screenshot({ path: "test-results/ax-editable-meeting-editor-card-e2e.png" });
  await card.getByRole("button", { name: "새 회의록 내용 수정" }).click();
  await card.getByRole("textbox", { name: "회의록 초안", exact: true }).fill("사람이 확정한 출시 범위와 담당자별 막힌 점을 확인한다.");
  await card.screenshot({ path: "test-results/ax-editable-meeting-note-open-e2e.png" });
  await page.screenshot({ path: "test-results/ax-editable-meeting-editor-e2e.png", fullPage: true });

  const confirmButton = card.getByRole("button", { name: "저장" });
  const [response] = await Promise.all([
    page.waitForResponse((candidate) => candidate.url().endsWith(`/api/action-items/${pending.action_id}/commands/confirm`)),
    confirmButton.click(),
  ]);
  const submitted = response.request().postDataJSON();
  if (response.status() !== 200) throw new Error(`Meeting confirm returned ${response.status()}: ${await response.text()}`);
  const firstReceipt = await response.json();

  const receipt = page.locator(`.ax-action-card[data-action-id="${pending.action_id}"][data-state="approved"]`);
  await receipt.getByRole("button", { name: "회의 상세 보기" }).waitFor({ timeout: 20_000 });
  await receipt.locator(".action-task-attachment-list li").filter({ hasText: linkName }).waitFor();
  await receipt.getByText("새 회의록", { exact: true }).waitFor();
  if (await receipt.getByRole("button", { name: "업무나 자료 첨부" }).count()
    || await receipt.getByRole("button", { name: /첨부 제외/ }).count()
    || await receipt.getByRole("button", { name: "새 회의록 제외" }).count()) {
    throw new Error("completed Meeting card kept draft mutation controls");
  }
  await receipt.screenshot({ path: "test-results/ax-meeting-complete-card-e2e.png" });
  await page.screenshot({ path: "test-results/ax-editable-meeting-receipt-e2e.png", fullPage: true });

  const ledger = await page.evaluate(async ({ actionId, meetingId }) => {
    const [detail, meeting, meetings] = await Promise.all([
      fetch(`/api/action-items/${actionId}`).then((result) => result.json()),
      fetch(`/api/meetings/${meetingId}`).then((result) => result.json()),
      fetch("/api/meetings").then((result) => result.json()),
    ]);
    return { detail, meeting, matching: meetings.filter((item) => item.kind === "meeting" && item.meeting_id === meetingId) };
  }, { actionId: pending.action_id, meetingId: firstReceipt.derived_meeting_id });
  if (ledger.detail.rounds.length !== 2 || ledger.matching.length !== 1 || ledger.meeting.version !== 1) {
    throw new Error(`edited proposal did not become one Meeting v1 on Submission 2: ${JSON.stringify(ledger)}`);
  }
  if (ledger.meeting.title !== finalTitle || ledger.meeting.visibility !== "private") {
    throw new Error(`confirmed Meeting lost human edits: ${JSON.stringify(ledger.meeting)}`);
  }
  if (ledger.meeting.note?.body !== "사람이 확정한 출시 범위와 담당자별 막힌 점을 확인한다." || ledger.meeting.note?.source_status !== "resolved") {
    throw new Error(`Meeting and frozen initial note were not committed together: ${JSON.stringify(ledger.meeting.note)}`);
  }
  const frozenEvidence = ledger.meeting.note?.versions?.[0]?.source_evidence ?? [];
  if (
    !frozenEvidence.some((source) => source.source_type === "meeting" && source.source_id === basis.meeting.meeting_id)
    || !frozenEvidence.some((source) => source.source_type === "task" && source.source_id === basis.task.task_id)
    || !frozenEvidence.some((source) => source.source_type === "material")
  ) {
    throw new Error(`Meeting note did not retain its authorized evidence: ${JSON.stringify(frozenEvidence)}`);
  }
  const confirmedLink = ledger.meeting.materials?.find((material) => material.source_kind === "external_link");
  const confirmedTask = ledger.meeting.materials?.find((material) => material.source_kind === "resource_ref");
  if (
    ledger.meeting.materials?.length !== 2
    || confirmedLink?.name !== linkName
    || confirmedLink?.url !== linkUrl
    || confirmedTask?.name !== basis.task.title
  ) {
    throw new Error(`Meeting and its staged attachment were not committed together: ${JSON.stringify(ledger.meeting.materials)}`);
  }
  if (ledger.meeting.recordings.length !== 0 || ledger.meeting.lineage?.source_action_item_id !== pending.action_id) {
    throw new Error(`Meeting confirmation crossed its local-effect boundary: ${JSON.stringify(ledger.meeting)}`);
  }

  // A lost confirm response replays the same receipt and cannot create another Meeting.
  const replay = await page.evaluate(async ({ actionId, body }) => {
    const answer = await fetch(`/api/action-items/${actionId}/commands/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return { status: answer.status, body: await answer.json() };
  }, { actionId: pending.action_id, body: submitted });
  if (replay.status !== 200 || replay.body.derived_meeting_id !== ledger.meeting.meeting_id) {
    throw new Error(`Meeting confirm retry was not the same receipt: ${JSON.stringify(replay)}`);
  }

  await receipt.getByRole("button", { name: "회의 상세 보기" }).click();
  await page.locator(".ax-drawer").waitFor({ state: "detached", timeout: 20_000 });
  const meetingDrawer = page.getByRole("dialog", { name: "회의 상세" });
  await meetingDrawer.getByRole("heading", { name: finalTitle, exact: true }).waitFor({ timeout: 20_000 });
  await meetingDrawer.getByText("AX 제안에서 생성됨").waitFor();
  const currentMaterials = meetingDrawer.getByRole("region", { name: "현재 회의 첨부" });
  const meetingLink = currentMaterials.getByRole("link", { name: linkName });
  await meetingLink.waitFor();
  if (await meetingLink.getAttribute("href") !== linkUrl || await currentMaterials.getByText(/현재 목록/).count() !== 1) {
    throw new Error("Meeting detail did not reopen the Meeting-owned current attachment list");
  }
  await meetingDrawer.getByRole("region", { name: "초기 회의록 근거" }).waitFor();
  await meetingDrawer.screenshot({ path: "test-results/ax-meeting-detail-current-materials-e2e.png" });
  const finalizedResponse = page.waitForResponse(
    (candidate) => candidate.url().endsWith(`/api/meetings/${ledger.meeting.meeting_id}/note/finalize`),
  );
  await meetingDrawer.getByRole("button", { name: "회의록 확정" }).click();
  const finalized = await finalizedResponse;
  if (!finalized.ok()) throw new Error(`Meeting note finalize returned ${finalized.status()}: ${await finalized.text()}`);
  const finalizedMeeting = await page.evaluate(
    async (meetingId) => fetch(`/api/meetings/${meetingId}`).then((response) => response.json()),
    ledger.meeting.meeting_id,
  );
  if (finalizedMeeting.note?.lifecycle !== "finalized" || finalizedMeeting.note?.version !== 1) {
    throw new Error(`human review did not finalize the frozen note: ${JSON.stringify(finalizedMeeting.note)}`);
  }

  console.log(JSON.stringify({
    result: "one inline edited Submission created one local Meeting and human-finalized its sourced initial note",
    action_id: pending.action_id,
    submission_versions: ledger.detail.rounds.map((round) => round.submission_version),
    meeting_id: ledger.meeting.meeting_id,
  }));
} finally {
  await browser.close();
}
