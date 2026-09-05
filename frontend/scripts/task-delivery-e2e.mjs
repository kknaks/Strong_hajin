import { chromium } from "@playwright/test";

import { loginAs, pollFor, switchAccount } from "./e2e-helpers.mjs";

// 끝냈다와 완료로 인정한다는 다른 사실이다 — the whole round trip in the browser: accept a request, do the work,
// report the result, have the requester ask for more, report again, and only then be accepted as done.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const title = `결과 확인 업무 ${stamp}`;

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

  await page.evaluate(async (subject) => {
    await fetch("/api/work-requests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: subject, assignee_id: "jiho", description: "분기 수치를 정리해 주세요" }),
    });
  }, title);

  // Jiho accepts and starts the work.
  await switchAccount(page, "jiho");
  const judgement = await pollFor(
    page,
    () =>
      page.evaluate(async (subject) => {
        const items = await (await fetch("/api/action-items")).json();
        return items.find((item) => item.subject === subject) ?? null;
      }, title),
    { timeout: 20_000, description: "the request to reach the reviewer" },
  );
  const taskId = await page.evaluate(async ({ actionItemId, expectedVersion, subject }) => {
    await fetch(`/api/action-items/${actionItemId}/commands/accept`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: expectedVersion }),
    });
    const rows = await (await fetch("/api/my-work")).json();
    const task = rows.find((row) => row.title === subject);
    await fetch(`/api/tasks/${task.task_id}/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: task.version }),
    });
    return task.task_id;
  }, { actionItemId: judgement.action_item_id, expectedVersion: judgement.expected_version, subject: title });

  // The holder reports the result rather than closing it.
  await page.reload({ waitUntil: "domcontentloaded" });
  await navigation.getByRole("button", { name: "내 업무" }).click();
  await page.getByRole("row", { name: new RegExp(title) }).click();
  const drawer = page.getByRole("dialog", { name: "업무 상세" });
  await drawer.waitFor({ timeout: 20_000 });
  if (await drawer.getByRole("button", { name: "완료 처리" }).count()) {
    throw new Error("the holder was offered to close work someone else asked for");
  }
  await drawer.getByRole("button", { name: "완료 보고" }).click();
  const form = drawer.locator("section[aria-label='완료 보고']");
  await form.getByLabel("결과 요약").fill("1차 수치를 정리했습니다");
  await form.getByRole("button", { name: "보고 보내기" }).click();
  await drawer.locator("section[aria-label='완료 확인 대기']").waitFor({ timeout: 20_000 });
  await drawer.getByRole("button", { name: "상세 닫기" }).click();

  // The requester meets a question of its own, and asks for more.
  await switchAccount(page, "mina");
  const review = await pollFor(
    page,
    () =>
      page.evaluate(async (subject) => {
        const items = await (await fetch("/api/action-items")).json();
        return items.find((item) => item.kind === "task.delivery" && item.subject === subject) ?? null;
      }, title),
    { timeout: 20_000, description: "the delivery review to reach the requester" },
  );
  if (review.operation_label !== "업무 결과 확인") throw new Error(`unexpected operation: ${review.operation_label}`);
  await navigation.getByRole("button", { name: "내 업무" }).click();
  const card = page.locator(`.decision-panel .task-card[data-action-item-id="${review.action_item_id}"]`);
  await card.waitFor({ timeout: 20_000 });
  await card.getByRole("button", { name: "판단하기" }).click();
  const judgeDrawer = page.getByRole("dialog", { name: "판단 상세" });
  await judgeDrawer.getByRole("button", { name: "보완 요청" }).waitFor({ timeout: 20_000 });
  const detailText = ((await judgeDrawer.textContent()) ?? "").replace(/\s+/g, " ");
  if (!detailText.includes("1차 수치를 정리했습니다")) throw new Error(`the reported result is not on the card: ${detailText}`);
  await judgeDrawer.getByRole("button", { name: "보완 요청", exact: true }).click();
  await judgeDrawer.getByLabel("보완 요청 사유").fill("지난달 수치가 빠졌습니다");
  await judgeDrawer.getByRole("button", { name: "보완 요청 확정" }).click();
  await page.getByRole("dialog").waitFor({ state: "detached", timeout: 20_000 });

  // The holder sees why, works on, and reports again — another round of the same question.
  await switchAccount(page, "jiho");
  await navigation.getByRole("button", { name: "내 업무" }).click();
  await page.getByRole("row", { name: new RegExp(title) }).click();
  const back = page.getByRole("dialog", { name: "업무 상세" });
  const reason = back.locator("section[aria-label='보완 필요']");
  await reason.waitFor({ timeout: 20_000 });
  if (!((await reason.textContent()) ?? "").includes("지난달 수치가 빠졌습니다")) throw new Error("the reason did not reach the holder");
  await back.getByRole("button", { name: "완료 보고" }).click();
  await back.locator("section[aria-label='완료 보고']").getByLabel("결과 요약").fill("지난달 수치를 채웠습니다");
  await back.locator("section[aria-label='완료 보고']").getByRole("button", { name: "보고 보내기" }).click();
  await back.locator("section[aria-label='완료 확인 대기']").waitFor({ timeout: 20_000 });
  await back.getByRole("button", { name: "상세 닫기" }).click();

  // The requester reads both rounds and accepts.
  await switchAccount(page, "mina");
  const second = await pollFor(
    page,
    () =>
      page.evaluate(async (subject) => {
        const items = await (await fetch("/api/action-items")).json();
        return items.find((item) => item.kind === "task.delivery" && item.subject === subject) ?? null;
      }, title),
    { timeout: 20_000, description: "the second report to reach the requester" },
  );
  if (second.action_item_id !== review.action_item_id) throw new Error("a second report opened a second question");
  if (second.submission_version !== 2) throw new Error(`unexpected round: ${second.submission_version}`);
  await navigation.getByRole("button", { name: "내 업무" }).click();
  const secondCard = page.locator(`.decision-panel .task-card[data-action-item-id="${second.action_item_id}"]`);
  await secondCard.getByRole("button", { name: "판단하기" }).click();
  const finalDrawer = page.getByRole("dialog", { name: "판단 상세" });
  const rounds = ((await finalDrawer.locator("section[aria-label='회차 기록']").textContent()) ?? "").replace(/\s+/g, " ");
  if (!rounds.includes("1차 수치를 정리했습니다") || !rounds.includes("지난달 수치를 채웠습니다")) {
    throw new Error(`both rounds are not readable: ${rounds}`);
  }
  await finalDrawer.getByRole("button", { name: "완료 인정" }).click();
  await page.getByRole("dialog").waitFor({ state: "detached", timeout: 20_000 });

  const closed = await page.evaluate(async (id) => {
    const task = await (await fetch(`/api/tasks/${id}`)).json();
    const items = await (await fetch("/api/action-items")).json();
    return {
      state: task.state,
      delivery: task.delivery?.status,
      rounds: task.delivery?.rounds,
      stillAsking: items.filter((row) => row.kind === "task.delivery" && row.resource?.id === id).length,
    };
  }, taskId);
  if (closed.state !== "done" || closed.delivery !== "resolved" || closed.rounds !== 2 || closed.stillAsking !== 0) {
    throw new Error(`the accepted result did not close the work: ${JSON.stringify(closed)}`);
  }

  await page.screenshot({ path: "test-results/task-delivery-e2e.png", fullPage: true });
  console.log(JSON.stringify({ result: "reported, sent back for more, reported again, then accepted as done", task_id: taskId, action_item_id: review.action_item_id }));
} finally {
  await browser.close();
}
