import { chromium } from "@playwright/test";

import { loginAs, switchAccount } from "./e2e-helpers.mjs";

const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const title = `Playwright 업무 요청 ${Date.now()}`;

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
  await page.getByRole("tab", { name: "요청" }).click();
  await page.getByLabel("요청할 업무").fill(title);
  await page.getByLabel("담당 후보").selectOption("jiho");
  await page.getByRole("button", { name: "업무 요청 보내기" }).click();
  const jihoInboxResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/action-inbox"),
  );
  await switchAccount(page, "jiho");
  const jihoInbox = await (await jihoInboxResponse).json();
  if (!jihoInbox.some((request) => request.title === title && request.state === "pending")) {
    throw new Error("Jiho did not receive the pending WorkRequest through the authorized inbox projection");
  }
  await navigation.getByRole("button", { name: "오늘" }).click();
  const todayRequestCard = page.locator(".card-stack .task-card", { hasText: title });
  await todayRequestCard.waitFor();
  if (await page.getByRole("button", { name: "일일보고 작성" }).count()) {
    throw new Error("Jiho must not receive a daily-report CTA without report capability");
  }
  // A double submit through the real UI: Enter, an auto-repeat Enter, and a click before React disables the button.
  // Exactly one comment must exist afterwards (client guard plus the server idempotency key).
  const commentBody = `논의 추가 ${Date.now()}`;
  await todayRequestCard.getByRole("button", { name: "검토하기" }).click();
  const requestDrawer = page.getByRole("dialog", { name: "업무 요청 상세" });
  await requestDrawer.waitFor();
  const commentField = requestDrawer.getByPlaceholder("무엇이 걸리는지 남긴다");
  await commentField.fill(commentBody);
  const commentPosts = [];
  page.on("request", (request) => {
    if (request.url().includes("/comments") && request.method() === "POST") commentPosts.push(request.url());
  });
  // Sequential Playwright actions cannot collide, so dispatch all three in one synchronous task, which is what a fast
  // user does: React has not re-rendered the disabled button when the second and third events arrive.
  await page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"]');
    const input = dialog.querySelector('input[placeholder="무엇이 걸리는지 남긴다"]');
    const button = Array.from(dialog.querySelectorAll("button")).find((item) => item.textContent.trim() === "남기기");
    const enter = (repeat) => input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true, repeat }));
    enter(false);
    enter(true);
    button.click();
  });
  await requestDrawer.getByText(commentBody).first().waitFor({ timeout: 10_000 });
  const storedComments = await page.evaluate(async ({ requestId, body }) => {
    const timeline = await (await fetch(`/api/work-requests/${requestId}/timeline`, { headers: { "X-Demo-Persona": "jiho" } })).json();
    return timeline.comments.filter((item) => item.body === body);
  }, { requestId: jihoInbox.find((item) => item.title === title).request_id, body: commentBody });
  if (storedComments.length !== 1 || commentPosts.length !== 1) {
    throw new Error(`double submit produced ${commentPosts.length} POSTs and ${storedComments.length} comments`);
  }
  await requestDrawer.getByRole("button", { name: "상세 닫기" }).click();

  await todayRequestCard.getByRole("button", { name: "수락" }).click();
  await navigation.getByRole("button", { name: "내 업무" }).click();
  const acceptedTask = page.locator("tr.progress-row", { hasText: title });
  await acceptedTask.waitFor();
  await acceptedTask.getByText("시작 전", { exact: true }).waitFor();
  console.log(JSON.stringify({ title, result: "accepted task projected to My Work", comment_posts: commentPosts.length, stored_comments: storedComments.length }));
} finally {
  await browser.close();
}
