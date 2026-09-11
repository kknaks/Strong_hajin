import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

// One viewport, one card, one immutable proposal history:
// AX summary → inline typed editor → atomic Task receipt → the real Task detail.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const originalTitle = `AX 편집 원안 ${stamp}`;
const finalTitle = `AX 편집 확정 ${stamp}`;

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "jiho");

  const basis = await page.evaluate(async (stampValue) => {
    const headers = { "Content-Type": "application/json" };
    const referenceResponse = await fetch("/api/tasks", {
      method: "POST",
      headers,
      body: JSON.stringify({ title: `편집 근거 업무 ${stampValue}` }),
    });
    const projectResponse = await fetch("/api/projects", {
      method: "POST",
      headers,
      body: JSON.stringify({ name: `편집 검증 프로젝트 ${stampValue}` }),
    });
    if (!referenceResponse.ok || !projectResponse.ok) {
      throw new Error(`fixture creation failed: task=${referenceResponse.status}, project=${projectResponse.status}`);
    }
    const reference = await referenceResponse.json();
    const project = await projectResponse.json();
    return { reference, project };
  }, stamp);

  const conversationsLoaded = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "GET",
  );
  await page.getByRole("button", { name: "AX", exact: true }).click();
  await conversationsLoaded;
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  await page.getByLabel("AX 메시지").fill(
    [
      "SCAX MCP의 task_create_self 도구를 실제로 호출해 내 업무 생성 제안을 만들어줘.",
      `제목은 '${originalTitle}', 내용은 'AX가 쓴 설명', 시작일은 2026-09-10, 기한은 2026-09-20,`,
      `프로젝트 id는 ${basis.project.project_id}, 참고 업무 id는 ${basis.reference.task_id}, 시작 단계는 '원안 확인'이야.`,
      "답변으로만 제안하지 말고 도구를 호출한 뒤, 내가 카드에서 확정할 때까지 기다려.",
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
      return current.actions?.find(
        (item) => item.action_type === "task.create_self" && item.state === "pending" && item.subject === title,
      ) ?? null;
    }, { conversationId: conversation.conversation_id, title: originalTitle }),
    { timeout: 120_000, description: "the editable task.create_self Action" },
  );
  const contract = pending.edit_contract;
  const fieldIds = contract?.fields?.map((field) => field.id);
  for (const required of ["title", "description", "assignee_id", "start_date", "due_date", "project_id", "checklist", "reference_task_ids"]) {
    if (!fieldIds?.includes(required)) throw new Error(`server edit contract lacks ${required}: ${JSON.stringify(fieldIds)}`);
  }
  const projectField = contract.fields.find((field) => field.id === "project_id");
  if (!projectField?.options?.some((option) => option.value === basis.project.project_id)) {
    throw new Error(`server contract omitted an authorized project option: ${JSON.stringify(projectField)}`);
  }
  if (!contract.values.reference_task_ids.includes(basis.reference.task_id)) {
    throw new Error(`proposal values lost reference identity: ${JSON.stringify(contract.values)}`);
  }

  const card = page.locator(`.ax-action-card[data-action-id="${pending.action_id}"]`);
  await card.waitFor({ timeout: 20_000 });
  const summaryLayout = await card.evaluate((element) => {
    const badges = [...element.querySelectorAll(".action-task-badges span")];
    const title = element.querySelector(".action-task-summary > b");
    const description = element.querySelector(".action-task-summary > p");
    const actions = element.querySelector(".action-task-actions");
    const buttons = [...element.querySelectorAll(".action-task-actions .btn")];
    const primary = element.querySelector(".action-task-actions .btn.primary");
    const attachmentTrigger = element.querySelector(".action-task-attachment-trigger");
    const attachmentGroup = element.querySelector(".action-task-attachments");
    const summaryRows = [...element.querySelectorAll(".action-task-summary dl > div")].map((row) => ({
      label: row.querySelector("dt")?.textContent?.trim(),
      value: row.querySelector("dd")?.textContent?.trim(),
    }));
    return {
      badges: badges.map((badge) => badge.textContent),
      drawerWidth: element.closest(".ax-drawer")?.getBoundingClientRect().width ?? 0,
      width: element.getBoundingClientRect().width,
      borderLeftWidth: getComputedStyle(element).borderLeftWidth,
      borderRadius: getComputedStyle(element).borderRadius,
      boxShadow: getComputedStyle(element).boxShadow,
      background: getComputedStyle(element).backgroundColor,
      titleFontSize: title ? getComputedStyle(title).fontSize : null,
      descriptionFontSize: description ? getComputedStyle(description).fontSize : null,
      hasDisclosure: Boolean(element.querySelector("details")),
      actionsRightAligned: actions ? getComputedStyle(actions).justifyContent : null,
      buttonHeights: buttons.map((button) => button.getBoundingClientRect().height),
      buttonRadii: buttons.map((button) => getComputedStyle(button).borderRadius),
      secondaryBorder: buttons[0] ? getComputedStyle(buttons[0]).borderColor : null,
      primaryBackground: primary ? getComputedStyle(primary).backgroundColor : null,
      primaryText: primary?.textContent,
      actionLabels: buttons.map((button) => button.textContent),
      attachmentText: attachmentTrigger?.textContent?.replace(/\s+/g, " ").trim(),
      attachmentEnabled: attachmentTrigger ? !attachmentTrigger.disabled : false,
      attachmentHeight: attachmentTrigger?.getBoundingClientRect().height ?? 0,
      attachmentWidth: attachmentTrigger?.getBoundingClientRect().width ?? 0,
      attachmentGroupWidth: attachmentGroup?.getBoundingClientRect().width ?? 0,
      summaryRows,
      checklistItems: [...element.querySelectorAll(".action-task-summary-checklist li")].map((item) => item.textContent?.trim()),
      summaryText: element.querySelector(".action-task-summary")?.textContent ?? "",
    };
  });
  if (
    JSON.stringify(summaryLayout.badges) !== JSON.stringify(["SC AX", "초안"])
    || summaryLayout.drawerWidth !== 520
    || summaryLayout.width !== 380
    || summaryLayout.borderLeftWidth !== "1px"
    || summaryLayout.borderRadius !== "14px"
    || summaryLayout.boxShadow !== "none"
    || summaryLayout.background !== "rgb(255, 255, 255)"
    || summaryLayout.titleFontSize !== "15px"
    || summaryLayout.descriptionFontSize !== "14px"
    || summaryLayout.hasDisclosure
    || summaryLayout.actionsRightAligned !== "flex-end"
    || summaryLayout.buttonHeights.some((height) => height !== 48)
    || summaryLayout.buttonRadii.some((radius) => radius !== "10px")
    || summaryLayout.secondaryBorder !== "rgba(112, 115, 124, 0.16)"
    || summaryLayout.primaryBackground !== "rgb(84, 103, 247)"
    || summaryLayout.primaryText !== "등록"
    || JSON.stringify(summaryLayout.actionLabels) !== JSON.stringify(["수정", "등록"])
    || summaryLayout.attachmentText !== "업무나 자료 첨부"
    || !summaryLayout.attachmentEnabled
    || summaryLayout.attachmentHeight !== 40
    || summaryLayout.attachmentWidth !== summaryLayout.attachmentGroupWidth
    || JSON.stringify(summaryLayout.summaryRows.map((row) => row.label)) !== JSON.stringify(["담당자", "기한", "체크리스트"])
    || summaryLayout.summaryRows.some((row) => /\([^()]+\)$/.test(row.value ?? ""))
    || JSON.stringify(summaryLayout.checklistItems) !== JSON.stringify(["원안 확인"])
    || summaryLayout.summaryText.includes("→")
  ) {
    throw new Error(`task summary card diverged from the accepted hierarchy: ${JSON.stringify(summaryLayout)}`);
  }
  await page.screenshot({ path: "test-results/ax-editable-task-summary-e2e.png", fullPage: true });
  await card.screenshot({ path: "test-results/ax-editable-task-summary-card-e2e.png" });

  // The full-width attachment affordance enters editing with the picker already open,
  // and cancel returns to the exact registration summary without invoking a command.
  await card.getByRole("button", { name: "업무나 자료 첨부" }).click();
  const emptyPicker = page.getByRole("dialog", { name: "업무나 자료 첨부" });
  await emptyPicker.getByText("찾고 싶은 업무나 자료를 검색하세요").waitFor();
  if (await emptyPicker.getByRole("button", { name: basis.reference.title, exact: true }).count()) {
    throw new Error("attachment picker exposed candidates before a search query");
  }
  await page.screenshot({ path: "test-results/ax-attachment-picker-empty-e2e.png", fullPage: true });
  for (const viewport of [{ width: 1279, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    const narrowLayout = await emptyPicker.evaluate((element) => {
      const shell = document.querySelector(".thesc-shell");
      const blocker = document.querySelector(".min-width-notice");
      const rect = element.getBoundingClientRect();
      return {
        shellDisplay: shell ? getComputedStyle(shell).display : null,
        blockerDisplay: blocker ? getComputedStyle(blocker).display : null,
        left: rect.left,
        right: rect.right,
        width: rect.width,
        viewport: window.innerWidth,
      };
    });
    if (
      narrowLayout.shellDisplay === "none"
      || (narrowLayout.blockerDisplay !== null && narrowLayout.blockerDisplay !== "none")
      || narrowLayout.left < 0
      || narrowLayout.right > narrowLayout.viewport
    ) {
      throw new Error(`narrow attachment picker was hidden or clipped: ${JSON.stringify(narrowLayout)}`);
    }
    await page.screenshot({ path: `test-results/ax-attachment-picker-empty-${viewport.width}-e2e.png`, fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 960 });
  await emptyPicker.getByRole("button", { name: "첨부 선택 닫기" }).click();
  await card.getByRole("button", { name: "취소" }).click();
  await card.getByRole("button", { name: "등록" }).waitFor();
  if (await card.getByLabel("업무 명").count() || await card.getByRole("button", { name: "거절" }).count()) {
    throw new Error("cancel did not return the editable Task to its registration summary");
  }

  await card.getByRole("button", { name: "수정" }).click();
  const title = card.getByLabel("업무 명");
  await title.waitFor();
  if (!(await title.evaluate((element) => element === document.activeElement))) {
    throw new Error("inline editor did not move focus to the first editable field");
  }
  await title.fill(finalTitle);
  if (await card.locator(".action-task-fields").evaluate((element) => getComputedStyle(element).gridTemplateColumns.includes(" "))) {
    throw new Error("task editor did not stay in one drawer-width column");
  }
  const editorLayout = await card.evaluate((element) => ({
    view: element.getAttribute("data-view"),
    borderColor: getComputedStyle(element).borderColor,
    inputHeight: element.querySelector("input")?.getBoundingClientRect().height ?? 0,
    textareaHeight: element.querySelector("textarea")?.getBoundingClientRect().height ?? 0,
    fieldIds: [...element.querySelectorAll(".action-task-fields > [data-field-id]")].map((field) => field.getAttribute("data-field-id")),
    dueValue: element.querySelector('[data-field-id="due_date"] input[type="text"]')?.value,
    dueRequired: element.querySelector('[data-field-id="due_date"] input[type="text"]')?.required,
    assigneeBackground: getComputedStyle(element.querySelector('[data-field-id="assignee_id"] output')).backgroundColor,
    assigneeBorder: getComputedStyle(element.querySelector('[data-field-id="assignee_id"] output')).borderLeftWidth,
    actionLabels: [...element.querySelectorAll(".action-task-actions .btn")].map((button) => button.textContent?.trim()),
    resetHasIcon: Boolean(element.querySelector(".action-task-reset svg")),
    primaryText: element.querySelector(".action-task-actions .btn.primary")?.textContent,
  }));
  if (
    editorLayout.view !== "editing"
    || editorLayout.borderColor !== "rgb(219, 211, 254)"
    || editorLayout.inputHeight !== 48
    || editorLayout.textareaHeight !== 72
    || JSON.stringify(editorLayout.fieldIds) !== JSON.stringify(["title", "description", "assignee_id", "start_date", "due_date", "project_id", "checklist"])
    || editorLayout.dueValue !== "2026.09.20"
    || !editorLayout.dueRequired
    || editorLayout.assigneeBackground !== "rgb(255, 255, 255)"
    || editorLayout.assigneeBorder !== "1px"
    || JSON.stringify(editorLayout.actionLabels) !== JSON.stringify(["취소", "초기화", "저장"])
    || !editorLayout.resetHasIcon
    || editorLayout.primaryText !== "저장"
  ) {
    throw new Error(`task editor shape diverged from the accepted form: ${JSON.stringify(editorLayout)}`);
  }
  await card.getByLabel("내용").fill("사람이 확정한 설명");
  await card.getByRole("textbox", { name: "시작일", exact: true }).fill("2026-09-12");
  await card.getByRole("textbox", { name: "기한", exact: true }).fill("2026-09-30");
  await card.getByLabel("프로젝트", { exact: true }).selectOption({ label: basis.project.name });
  await card.getByLabel("추가할 단계").fill("최종 검토");
  await card.getByRole("button", { name: "단계 추가" }).click();
  const selectedReference = card.locator('[data-attachment-kind="task"]', { hasText: basis.reference.title });
  await selectedReference.waitFor();
  await card.getByRole("button", { name: `${basis.reference.title} 첨부 제외` }).click();
  const emptyAttachment = await card.evaluate((element) => {
    const trigger = element.querySelector(".action-task-attachment-trigger.empty");
    return {
      hasEmptyMessage: element.textContent?.includes("첨부가 없습니다") ?? false,
      color: trigger ? getComputedStyle(trigger).color : null,
      borderColor: trigger ? getComputedStyle(trigger).borderColor : null,
    };
  });
  if (
    emptyAttachment.hasEmptyMessage
    || emptyAttachment.color !== "rgb(109, 116, 131)"
    || emptyAttachment.borderColor !== "rgb(232, 234, 240)"
  ) {
    throw new Error(`empty attachment control was not visually quiet: ${JSON.stringify(emptyAttachment)}`);
  }
  await card.screenshot({ path: "test-results/ax-editable-task-empty-attachment-card-e2e.png" });
  await card.getByRole("button", { name: "업무나 자료 첨부" }).click();
  const resultPicker = page.getByRole("dialog", { name: "업무나 자료 첨부" });
  await resultPicker.getByLabel("업무나 자료 검색").fill(basis.reference.title);
  await resultPicker.getByRole("button", { name: new RegExp(basis.reference.title) }).click();
  await page.screenshot({ path: "test-results/ax-attachment-picker-result-e2e.png", fullPage: true });
  await resultPicker.getByRole("button", { name: "첨부", exact: true }).click();
  await page.setViewportSize({ width: 1280, height: 1460 });
  await card.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/ax-editable-task-editor-e2e.png", fullPage: true });
  await card.screenshot({ path: "test-results/ax-editable-task-editor-card-e2e.png" });

  const confirmButton = card.getByRole("button", { name: "저장" });
  await confirmButton.scrollIntoViewIfNeeded();
  const [response] = await Promise.all([
    page.waitForResponse(
      (candidate) => candidate.url().endsWith(`/api/action-items/${pending.action_id}/commands/confirm`),
    ),
    confirmButton.click(),
  ]);
  const submitted = response.request().postDataJSON();
  if (response.status() !== 200) throw new Error(`confirm returned ${response.status()}: ${await response.text()}`);

  const receipt = page.locator(`.ax-action-card[data-action-id="${pending.action_id}"][data-state="approved"]`);
  await receipt.getByRole("button", { name: "업무 상세보기" }).waitFor({ timeout: 20_000 });
  const completionNote = page.locator(".action-task-completion-note").filter({ hasText: "업무가 등록되었습니다." });
  await completionNote.waitFor();
  const completedControls = await receipt.evaluate((element) => ({
    attachmentTrigger: Boolean([...element.querySelectorAll("button")].find((button) => button.textContent?.includes("업무나 자료 첨부"))),
    edit: Boolean([...element.querySelectorAll("button")].find((button) => button.textContent?.trim() === "수정")),
    register: Boolean([...element.querySelectorAll("button")].find((button) => button.textContent?.trim() === "등록")),
  }));
  if (completedControls.attachmentTrigger || completedControls.edit || completedControls.register) {
    throw new Error(`completed Task card kept draft controls: ${JSON.stringify(completedControls)}`);
  }
  await page.screenshot({ path: "test-results/ax-editable-task-receipt-e2e.png", fullPage: true });
  await receipt.screenshot({ path: "test-results/ax-editable-task-receipt-card-e2e.png" });

  const ledger = await page.evaluate(async ({ actionId, titleValue }) => {
    const [detail, work] = await Promise.all([
      fetch(`/api/action-items/${actionId}`).then((result) => result.json()),
      fetch("/api/my-work").then((result) => result.json()),
    ]);
    return { detail, matching: work.filter((task) => task.title === titleValue) };
  }, { actionId: pending.action_id, titleValue: finalTitle });
  if (ledger.detail.rounds.length !== 2 || ledger.matching.length !== 1 || ledger.matching[0].version !== 1) {
    throw new Error(`edited proposal did not become one Task v1 on Submission 2: ${JSON.stringify(ledger)}`);
  }
  if (ledger.matching[0].project_id !== basis.project.project_id) {
    throw new Error(`created Task lost its project: ${JSON.stringify(ledger.matching[0])}`);
  }

  // A lost confirm response gets the same receipt, never a second Task.
  const replay = await page.evaluate(async ({ actionId, body }) => {
    const answer = await fetch(`/api/action-items/${actionId}/commands/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return { status: answer.status, body: await answer.json() };
  }, { actionId: pending.action_id, body: submitted });
  if (replay.status !== 200 || replay.body.derived_task_id !== ledger.matching[0].task_id) {
    throw new Error(`confirm retry was not the same receipt: ${JSON.stringify(replay)}`);
  }

  await receipt.getByRole("button", { name: "업무 상세보기" }).click();
  await page.locator(".ax-drawer").waitFor({ state: "detached", timeout: 20_000 });
  const taskDrawer = page.getByRole("dialog", { name: "업무 상세" });
  await taskDrawer.getByRole("heading", { name: finalTitle, exact: true }).waitFor({ timeout: 20_000 });
  await taskDrawer.getByRole("button", { name: "상세 닫기" }).click();
  await page.locator(".assistant-launcher-character").click();
  const reopenedReceipt = page.locator(`.ax-action-card[data-action-id="${pending.action_id}"][data-state="approved"]`);
  await reopenedReceipt.getByRole("button", { name: "업무 상세보기" }).waitFor({ timeout: 20_000 });
  await page.locator(".action-task-completion-note").filter({ hasText: "업무가 등록되었습니다." }).waitFor();

  console.log(JSON.stringify({
    result: "one inline edited Submission created one Task and left a navigable persistent receipt",
    action_id: pending.action_id,
    submission_versions: ledger.detail.rounds.map((round) => round.submission_version),
    task_id: ledger.matching[0].task_id,
  }));
} finally {
  await browser.close();
}
