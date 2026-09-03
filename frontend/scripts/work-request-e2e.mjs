import { chromium } from "@playwright/test";

const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const title = `Playwright 업무 요청 ${Date.now()}`;

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  const navigation = page.getByRole("navigation", { name: "제품 탐색" });
  await navigation.getByRole("button", { name: "내 업무" }).click();
  await page.getByLabel("요청할 업무").fill(title);
  await page.getByLabel("담당 후보").selectOption("jiho");
  await page.getByRole("button", { name: "업무 요청 보내기" }).click();
  await page.getByLabel("사용자").selectOption("jiho");
  await navigation.getByRole("button", { name: "오늘" }).click();
  await page.getByText(title).waitFor();
  if (await page.getByRole("button", { name: "일일보고 작성" }).count()) {
    throw new Error("Jiho must not receive a daily-report CTA without report capability");
  }
  await navigation.getByRole("button", { name: "판단" }).click();
  await page.getByText(title).waitFor();
  await page.getByRole("button", { name: "수락" }).last().click();
  await navigation.getByRole("button", { name: "내 업무" }).click();
  await page.getByText(title).waitFor();
  await page.getByText("열림").last().waitFor();
  console.log(JSON.stringify({ title, result: "accepted task projected to My Work" }));
} finally {
  await browser.close();
}
