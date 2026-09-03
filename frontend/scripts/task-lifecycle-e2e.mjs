import { chromium } from "@playwright/test";

import { loginAs, switchAccount } from "./e2e-helpers.mjs";

const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const title = `Playwright 직접 업무 ${Date.now()}`;

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
  await page.getByRole("button", { name: "새 업무 추가" }).click();
  await page.getByLabel("업무 제목").fill(title);
  await page.getByRole("button", { name: "업무 추가", exact: true }).click();
  await page.locator("tr.progress-row", { hasText: title }).waitFor();
  const taskRow = page.locator("tr.progress-row", { hasText: title });

  await taskRow.getByRole("button", { name: "시작" }).click();
  await taskRow.getByText("진행 중", { exact: true }).waitFor();
  await taskRow.getByRole("button", { name: "막힘" }).click();
  await taskRow.getByLabel("막힘 사유").fill("외부 자료 응답 대기");
  await taskRow.getByRole("button", { name: "막힘 처리" }).click();
  await taskRow.getByText("막힘", { exact: true }).waitFor();
  await taskRow.getByText("막힘 사유: 외부 자료 응답 대기").waitFor();
  await taskRow.getByRole("button", { name: "재개" }).click();
  await taskRow.getByText("진행 중", { exact: true }).waitFor();
  await taskRow.getByRole("button", { name: "완료" }).click();
  await taskRow.getByText("완료", { exact: true }).waitFor();
  await page.screenshot({ path: "test-results/task-lifecycle-e2e.png", fullPage: true });
  console.log(JSON.stringify({ title, result: "direct task lifecycle completed" }));
} finally {
  await browser.close();
}
