import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

// AX proposal without a note -> Figma-shaped Meeting card -> Meeting-owned attachment.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const title = `AX 첨부 회의 ${stamp}`;
const description = "회의록 없이 안건과 첨부 자료부터 준비합니다.";
const linkName = `회의 참고 링크 ${stamp}`;
const linkUrl = `https://example.com/meetings/${stamp}`;

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");

  await page.getByRole("button", { name: "AX", exact: true }).click();
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  await page.getByLabel("AX 메시지").fill(
    [
      "SCAX MCP의 meeting_create 도구를 실제로 호출해 로컬 회의 생성 제안을 만들어줘.",
      `organization_id는 scax, 제목은 '${title}', 시작은 2026-10-10T05:00:00Z, 종료는 2026-10-10T06:00:00Z,`,
      `공개 범위는 private, 참석자는 jiho이고, 내용은 '${description}'야.`,
      "초기 회의록은 만들지 말고 초대나 알림도 보내지 마. 내가 카드에서 확정할 때까지 기다려.",
    ].join(" "),
  );
  const createConversation = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "보내기" }).click();
  const conversation = await (await createConversation).json();

  const pending = await pollFor(
    page,
    () => page.evaluate(async ({ conversationId, expectedTitle }) => {
      const response = await fetch(`/api/conversations/${conversationId}`);
      if (!response.ok) return null;
      const current = await response.json();
      return current.actions?.find(
        (item) => item.action_type === "meeting.create" && item.state === "pending" && item.subject === expectedTitle,
      ) ?? null;
    }, { conversationId: conversation.conversation_id, expectedTitle: title }),
    { timeout: 120_000, description: "the no-note meeting.create Action" },
  );
  if (pending.edit_contract?.values.include_initial_note !== false) {
    throw new Error(`meeting proposal unexpectedly included a note: ${JSON.stringify(pending.edit_contract?.values)}`);
  }
  if (pending.edit_contract?.values.description !== description) {
    throw new Error(`meeting proposal lost its description: ${JSON.stringify(pending.edit_contract?.values)}`);
  }

  const card = page.locator(`.ax-action-card[data-action-id="${pending.action_id}"]`);
  await card.waitFor({ timeout: 20_000 });
  await card.getByText("회의", { exact: true }).waitFor();
  await card.getByText(title, { exact: true }).waitFor();
  await card.getByText(description, { exact: true }).waitFor();
  await card.getByText("2026.10.10", { exact: true }).waitFor();
  await card.getByText("14:00 - 15:00", { exact: true }).waitFor();
  await card.getByRole("button", { name: "업무나 자료 첨부" }).waitFor();
  await card.getByRole("button", { name: "회의록 추가" }).waitFor();
  await card.getByRole("button", { name: "수정" }).waitFor();
  await card.getByRole("button", { name: "등록" }).waitFor();
  const width = await card.evaluate((element) => element.getBoundingClientRect().width);
  if (width < 378 || width > 382) throw new Error(`Meeting card width drifted from the Figma frame: ${width}`);
  await card.screenshot({ path: "test-results/ax-meeting-draft-no-attachment-e2e.png" });

  await card.getByRole("button", { name: "업무나 자료 첨부" }).click();
  const picker = page.getByRole("dialog", { name: "업무나 자료 첨부" });
  await picker.getByRole("tab", { name: "링크 추가" }).click();
  await picker.getByLabel("링크 주소").fill(linkUrl);
  await picker.getByLabel("링크 이름").fill(linkName);
  await picker.getByRole("button", { name: "링크 추가" }).click();
  await card.getByText(linkName, { exact: true }).waitFor();

  const confirm = page.waitForResponse(
    (response) => response.url().endsWith(`/api/action-items/${pending.action_id}/commands/confirm`),
  );
  await card.getByRole("button", { name: "저장" }).click();
  const response = await confirm;
  if (response.status() !== 200) throw new Error(`Meeting confirm returned ${response.status()}: ${await response.text()}`);
  const receipt = await response.json();

  const ledger = await page.evaluate(async ({ meetingId }) => {
    const [meeting, meetings] = await Promise.all([
      fetch(`/api/meetings/${meetingId}`).then((result) => result.json()),
      fetch("/api/meetings").then((result) => result.json()),
    ]);
    return {
      meeting,
      matching: meetings.filter((item) => item.kind === "meeting" && item.meeting_id === meetingId),
    };
  }, { meetingId: receipt.derived_meeting_id });
  if (ledger.matching.length !== 1 || ledger.meeting.note !== null) {
    throw new Error(`no-note proposal did not become exactly one note-free Meeting: ${JSON.stringify(ledger)}`);
  }
  if (ledger.meeting.description !== description) {
    throw new Error(`Meeting detail lost the description: ${JSON.stringify(ledger.meeting)}`);
  }
  const linked = ledger.meeting.materials?.filter((item) => item.source_kind === "external_link") ?? [];
  if (linked.length !== 1 || linked[0].name !== linkName || linked[0].url !== linkUrl) {
    throw new Error(`staged link was not claimed by the Meeting: ${JSON.stringify(ledger.meeting.materials)}`);
  }
  await card.screenshot({ path: "test-results/ax-meeting-draft-with-attachment-receipt-e2e.png" });

  console.log(JSON.stringify({
    result: "one note-free Meeting claimed one staged link",
    action_id: pending.action_id,
    meeting_id: ledger.meeting.meeting_id,
    card_width: width,
  }));
} finally {
  await browser.close();
}
