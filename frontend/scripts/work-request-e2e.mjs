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
  await todayRequestCard.getByRole("button", { name: "수락" }).click();
  await navigation.getByRole("button", { name: "내 업무" }).click();
  const acceptedTask = page.locator("tr.progress-row", { hasText: title });
  await acceptedTask.waitFor();
  await acceptedTask.getByText("시작 전", { exact: true }).waitFor();
  console.log(JSON.stringify({ title, result: "accepted task projected to My Work" }));
} finally {
  await browser.close();
}
