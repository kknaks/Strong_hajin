import { chromium } from "@playwright/test";

import { chooseOption, loginAs, switchAccount } from "./e2e-helpers.mjs";

// The whole judgement round trip on one ActionItem:
// 요청 생성 → 담당자 조정 요청 → 요청자 수정·diff 확인·재상신 → 담당자 최신 회차 수락 → Task 한 건 → 양방향 lineage.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const title = `판단 통합 요청 ${stamp}`;
const revisedTitle = `${title} (조정 반영)`;
const adjustReason = "기한을 늦춰 주세요";
const proposedDue = "2026/12/24";
const comment = `근거 자료가 있나요? ${stamp}`;

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

const asPersona = (page, persona, path, init) =>
  page.evaluate(
    async ({ persona, path, init }) => {
      const response = await fetch(path, { ...init, headers: { "X-Demo-Persona": persona, ...(init?.headers ?? {}) } });
      return { status: response.status, body: await response.json().catch(() => null) };
    },
    { persona, path, init },
  );

try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");
  const navigation = page.getByRole("navigation", { name: "제품 탐색" });
  await navigation.getByRole("button", { name: "내 업무" }).click();

  // Mina asks Jiho for work.
  await page.getByRole("button", { name: "새 업무 추가" }).click();
  await page.getByRole("tab", { name: "요청", exact: true }).click();
  await page.getByLabel("요청할 업무").fill(title);
  await chooseOption(page, "담당 후보", /지호/);
  await page.getByRole("button", { name: "업무 요청 보내기" }).click();

  // Jiho meets it in the one judgement ledger and asks for a change.
  await switchAccount(page, "jiho");
  await navigation.getByRole("button", { name: "내 업무" }).click();
  const jihoCard = page.locator(".decision-section .task-card", { hasText: title });
  await jihoCard.waitFor({ timeout: 20_000 });
  if ((await jihoCard.locator(".task-card-kicker").textContent())?.trim() !== "업무 요청") {
    throw new Error("the judgement card is not labelled by the server operation");
  }
  const actionItemId = await jihoCard.getAttribute("data-action-item-id");
  const opened = await asPersona(page, "jiho", `/api/action-items/${actionItemId}`);
  const written = await asPersona(page, "jiho", `/api/work-requests/${opened.body.resource.id}/comments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body: comment }),
  });
  if (written.status !== 201) throw new Error(`the discussion could not be started: ${JSON.stringify(written)}`);
  const commentId = written.body.comment_id;

  await jihoCard.getByRole("button", { name: "판단하기" }).click();
  const jihoDrawer = page.getByRole("dialog", { name: "판단 상세" });
  // Talking is not judging: the comment is on the judgement and the question is still open.
  await jihoDrawer.getByLabel("논의").getByText(comment).waitFor({ timeout: 10_000 });
  await jihoDrawer.getByRole("button", { name: "조정 요청" }).click();
  await jihoDrawer.getByLabel("조정 요청 사유").fill(adjustReason);
  await jihoDrawer.getByLabel("제안: 희망 기한", { exact: true }).fill(proposedDue);
  await jihoDrawer.getByRole("button", { name: "조정 요청 확정" }).click();
  await page.getByRole("dialog").waitFor({ state: "detached", timeout: 20_000 });
  await page.locator(".decision-section .task-card", { hasText: title }).waitFor({ state: "detached", timeout: 20_000 });

  // The same question is now Mina's to answer, marked 조정 필요 — and Jiho, who may still read it, is offered nothing.
  const waiting = await asPersona(page, "jiho", `/api/action-items/${actionItemId}`);
  if (waiting.body.allowed_commands.length !== 0 || waiting.body.waiting_on.member_id !== "mina") {
    throw new Error(`commands were offered to someone whose turn it is not: ${JSON.stringify(waiting.body.allowed_commands)}`);
  }
  await switchAccount(page, "mina");
  await navigation.getByRole("button", { name: "내 업무" }).click();
  const minaCard = page.locator(`.decision-section .task-card[data-action-item-id="${actionItemId}"]`);
  await minaCard.waitFor({ timeout: 20_000 });
  if (!(await minaCard.textContent())?.includes("조정 필요")) {
    throw new Error("the adjusted request is not shown as 조정 필요 to the requester");
  }
  await minaCard.getByRole("button", { name: "판단하기" }).click();
  const minaDrawer = page.getByRole("dialog", { name: "판단 상세" });
  // The reason and the fields the reviewer proposed are both on the first screen, before any form is opened.
  const ask = minaDrawer.getByLabel("조정 요청");
  await ask.getByText(adjustReason).waitFor({ timeout: 10_000 });
  if (!(await ask.textContent())?.includes(proposedDue)) {
    throw new Error(`the structured proposal did not reach the requester: ${JSON.stringify(await ask.textContent())}`);
  }
  await minaDrawer.getByRole("button", { name: "수정안 재상신" }).click();
  const titleField = minaDrawer.getByLabel("요청할 업무");
  if ((await titleField.inputValue()) !== title) throw new Error("the revision form was not prefilled with the current round");
  // Applying the proposal is the requester's own act, and it fills the field the reviewer named.
  await minaDrawer.getByRole("button", { name: "제안대로 채우기" }).click();
  // React commits the applied proposal a tick after the click, so read the field until it settles.
  const dueField = minaDrawer.getByLabel("희망 기한", { exact: true });
  let filledDue = "";
  for (let attempt = 0; attempt < 40 && filledDue !== proposedDue; attempt += 1) {
    filledDue = await dueField.inputValue();
    if (filledDue !== proposedDue) await page.waitForTimeout(100);
  }
  if (filledDue !== proposedDue) {
    throw new Error(`제안대로 채우기 did not fill the proposed due date in YYYY/MM/DD: ${JSON.stringify(filledDue)}`);
  }
  await titleField.fill(revisedTitle);
  // The diff is visible before sending.
  const summary = minaDrawer.getByLabel("제출 전 변경 요약");
  await summary.getByText(revisedTitle).waitFor({ timeout: 10_000 });
  await minaDrawer.getByRole("button", { name: "수정안 재상신" }).click();
  await page.getByRole("dialog").waitFor({ state: "detached", timeout: 20_000 });

  // Jiho sees round two with the diff, and accepts it.
  await switchAccount(page, "jiho");
  await navigation.getByRole("button", { name: "내 업무" }).click();
  const roundTwo = page.locator(`.decision-section .task-card[data-action-item-id="${actionItemId}"]`);
  await roundTwo.waitFor({ timeout: 20_000 });
  if (!(await roundTwo.textContent())?.includes("2회차")) throw new Error("the reviewer did not receive the second round");
  await roundTwo.getByRole("button", { name: "판단하기" }).click();
  const acceptDrawer = page.getByRole("dialog", { name: "판단 상세" });
  const beforeAccept = await asPersona(page, "jiho", `/api/action-items/${actionItemId}`);
  const acceptedVersion = beforeAccept.body.expected_version;
  const history = acceptDrawer.getByLabel("회차 기록");
  await history.locator('[data-submission-version="1"]').waitFor();
  await history.locator('[data-submission-version="2"]').waitFor();
  const firstRound = await history.locator('[data-submission-version="1"]').textContent();
  if (!firstRound?.includes(adjustReason) || !firstRound?.includes(title)) {
    throw new Error(`the first round lost its content or decision: ${JSON.stringify(firstRound)}`);
  }
  await acceptDrawer.getByRole("button", { name: "수락" }).click();
  await page.getByRole("dialog").waitFor({ state: "detached", timeout: 20_000 });
  await page.locator(`.decision-section .task-card[data-action-item-id="${actionItemId}"]`).waitFor({ state: "detached", timeout: 20_000 });

  // A lost response is a receipt, not a second Task: the same command re-sent against the same version.
  const resent = await asPersona(page, "jiho", `/api/action-items/${actionItemId}/commands/accept`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_version: acceptedVersion }),
  });
  if (resent.status !== 200 || resent.body.status !== "resolved") {
    throw new Error(`a re-sent command was not answered with a receipt: ${JSON.stringify(resent)}`);
  }

  // Exactly one Task, and the ledger tells the whole story from both ends.
  const work = await asPersona(page, "jiho", "/api/my-work");
  const created = work.body.filter((task) => task.title === revisedTitle);
  if (created.length !== 1) throw new Error(`expected one Task, found ${created.length}`);
  const detail = await asPersona(page, "jiho", `/api/action-items/${actionItemId}`);
  const rounds = detail.body.rounds;
  if (detail.body.status !== "resolved" || rounds.length !== 2) {
    throw new Error(`the ActionItem did not resolve on two rounds: ${JSON.stringify({ status: detail.body.status, rounds: rounds.length })}`);
  }
  if (rounds[0].snapshot.title !== title || rounds[1].snapshot.title !== revisedTitle) {
    throw new Error("the immutable rounds do not carry both versions of the content");
  }
  if (rounds[0].decisions[0]?.decision !== "negotiate" || rounds[0].decisions[0]?.reason !== adjustReason) {
    throw new Error("the adjustment decision was not preserved on its own round");
  }
  if (rounds[0].decisions[0]?.suggested_changes?.due_date !== "2026-12-24") {
    throw new Error(`the structured proposal was not preserved on its round: ${JSON.stringify(rounds[0].decisions[0])}`);
  }
  if (rounds[1].decisions[0]?.decision !== "accept") throw new Error("the acceptance was not recorded on the latest round");
  if (rounds[1].diff?.title?.before !== title || rounds[1].diff?.title?.after !== revisedTitle) {
    throw new Error("the round diff was not preserved");
  }
  // Task ← ActionItem and ActionItem → request: both ends resolve to the same work.
  const requests = await asPersona(page, "mina", "/api/work-requests");
  const request = requests.body.find((row) => row.request_id === detail.body.resource.id);
  if (!request || request.title !== revisedTitle || request.state !== "accepted") {
    throw new Error(`the request ledger and the ActionItem disagree: ${JSON.stringify(request)}`);
  }
  if (detail.body.discussion.length !== 1 || detail.body.discussion[0].comment_id !== commentId) {
    throw new Error(`the discussion did not keep its identity across the rounds: ${JSON.stringify(detail.body.discussion)}`);
  }
  const timeline = await asPersona(page, "mina", `/api/work-requests/${request.request_id}/timeline`);
  if (timeline.body.submissions.length !== 2 || timeline.body.review_decisions.length !== 2) {
    throw new Error("the request timeline lost a round or a decision");
  }

  await page.screenshot({ path: "test-results/action-item-e2e.png", fullPage: true });
  console.log(
    JSON.stringify({
      result: "one ActionItem carried an adjustment round trip to one Task",
      action_item_id: actionItemId,
      rounds: rounds.length,
      task_id: created[0].task_id,
      request_id: request.request_id,
    }),
  );
} finally {
  await browser.close();
}
