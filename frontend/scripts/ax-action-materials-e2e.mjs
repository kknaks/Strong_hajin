import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const proposalTitle = `AX 첨부 원안 ${stamp}`;
const finalTitle = `AX 첨부 확정 ${stamp}`;
const linkLabel = `기획 링크 ${stamp}`;
const fileName = `검토안-${stamp}.txt`;
const fileBody = `action material ${stamp}`;

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "jiho");
  const reference = await page.evaluate(async (title) => {
    const response = await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    if (!response.ok) throw new Error(`reference fixture failed: ${response.status}`);
    return response.json();
  }, `첨부 근거 업무 ${stamp}`);

  const conversationsLoaded = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "GET",
  );
  await page.getByRole("button", { name: "AX", exact: true }).click();
  await conversationsLoaded;
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  await page.getByLabel("AX 메시지").fill(
    `SCAX MCP task_create_self 도구를 호출해 제목 '${proposalTitle}'인 내 업무 생성 제안을 만들고 카드 확정을 기다려.`,
  );
  const created = page.waitForResponse((response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST");
  await page.getByRole("button", { name: "보내기" }).click();
  const conversation = await (await created).json();

  const pending = await pollFor(
    page,
    () => page.evaluate(async ({ conversationId, title }) => {
      const response = await fetch(`/api/conversations/${conversationId}`);
      if (!response.ok) return null;
      const current = await response.json();
      return current.actions?.find((item) => item.action_type === "task.create_self" && item.state === "pending" && item.subject === title) ?? null;
    }, { conversationId: conversation.conversation_id, title: proposalTitle }),
    { timeout: 120_000, description: "the Task proposal for material staging" },
  );

  const card = page.locator(`.ax-action-card[data-action-id="${pending.action_id}"]`);
  await card.waitFor({ timeout: 20_000 });
  await card.getByRole("button", { name: "수정" }).click();
  await card.getByLabel("업무 명").fill(finalTitle);
  await card.getByRole("textbox", { name: "기한", exact: true }).fill("2026.09.30");
  await card.getByRole("button", { name: "업무나 자료 첨부" }).click();
  let picker = page.getByRole("dialog", { name: "업무나 자료 첨부" });
  await picker.getByLabel("업무나 자료 검색").fill(reference.title);
  await picker.getByRole("button", { name: new RegExp(reference.title) }).click();
  await picker.getByRole("button", { name: "첨부", exact: true }).click();

  await card.getByRole("button", { name: "업무나 자료 첨부" }).click();
  picker = page.getByRole("dialog", { name: "업무나 자료 첨부" });
  await picker.getByRole("tab", { name: "링크 추가" }).click();
  await picker.getByLabel("링크 주소").fill(" https://example.com/action-material ");
  await picker.getByLabel("링크 이름").fill(linkLabel);
  const linkResponse = page.waitForResponse((response) => response.url().endsWith(`/api/action-items/${pending.action_id}/material-drafts/links`));
  await picker.getByRole("button", { name: "링크 추가" }).click();
  if ((await linkResponse).status() !== 201) throw new Error("link draft staging failed");

  await card.getByRole("button", { name: "업무나 자료 첨부" }).click();
  picker = page.getByRole("dialog", { name: "업무나 자료 첨부" });
  await picker.getByRole("tab", { name: "파일 추가" }).click();
  const fileResponse = page.waitForResponse((response) => response.url().endsWith(`/api/action-items/${pending.action_id}/material-drafts/files`));
  await picker.getByLabel("첨부할 파일").setInputFiles({ name: fileName, mimeType: "text/plain", buffer: Buffer.from(fileBody) });
  if ((await fileResponse).status() !== 201) throw new Error("file draft staging failed");
  await card.getByText(fileName).waitFor();
  await page.screenshot({ path: "test-results/ax-action-materials-editor-e2e.png", fullPage: true });

  const confirm = card.getByRole("button", { name: "저장" });
  await pollFor(page, () => confirm.isEnabled(), { timeout: 20_000, description: "all attachment uploads to finish" });
  const confirmed = page.waitForResponse((response) => response.url().endsWith(`/api/action-items/${pending.action_id}/commands/confirm`));
  await confirm.click();
  const response = await confirmed;
  const submitted = response.request().postDataJSON();
  if (response.status() !== 200 || submitted.attachment_draft_ids?.length !== 2) {
    throw new Error(`confirm did not select both staged materials: ${response.status()} ${JSON.stringify(submitted)}`);
  }
  if (!submitted.draft?.reference_task_ids?.includes(reference.task_id)) {
    throw new Error(`confirm lost the reference Task: ${JSON.stringify(submitted)}`);
  }

  const receipt = page.locator(`.ax-action-card[data-action-id="${pending.action_id}"][data-state="approved"]`);
  await receipt.getByRole("button", { name: "업무 상세보기" }).waitFor({ timeout: 20_000 });
  await page.screenshot({ path: "test-results/ax-action-materials-receipt-e2e.png", fullPage: true });

  const ledger = await page.evaluate(async ({ actionId, expectedTitle }) => {
    const detail = await fetch(`/api/action-items/${actionId}`).then((answer) => answer.json());
    const taskId = detail.derived_task_id;
    const [task, materials] = await Promise.all([
      fetch(`/api/tasks/${taskId}`).then((answer) => answer.json()),
      fetch(`/api/tasks/${taskId}/materials`).then((answer) => answer.json()),
    ]);
    if (task.title !== expectedTitle) throw new Error(`wrong Task title: ${JSON.stringify(task)}`);
    return { detail, task, materials };
  }, { actionId: pending.action_id, expectedTitle: finalTitle });
  if (ledger.detail.rounds.length !== 2 || ledger.materials.length !== 2 || ledger.task.references.length !== 1) {
    throw new Error(`creation did not atomically claim every attachment: ${JSON.stringify(ledger)}`);
  }
  if (new Set(ledger.materials.map((row) => row.source_kind)).size !== 2) {
    throw new Error(`canonical material types collapsed: ${JSON.stringify(ledger.materials)}`);
  }
  const file = ledger.materials.find((row) => row.source_kind === "file");
  const downloaded = await page.evaluate(async ({ taskId, materialId }) => {
    const answer = await fetch(`/api/tasks/${taskId}/materials/${materialId}/content`);
    return { status: answer.status, body: await answer.text() };
  }, { taskId: ledger.task.task_id, materialId: file.material_id });
  if (downloaded.status !== 200 || downloaded.body !== fileBody) throw new Error(`claimed file bytes were not readable: ${JSON.stringify(downloaded)}`);

  console.log(JSON.stringify({
    result: "one Action attachment group claimed a Task reference, link, and file",
    action_id: pending.action_id,
    task_id: ledger.task.task_id,
    material_kinds: ledger.materials.map((row) => row.source_kind),
  }));
} finally {
  await browser.close();
}
