import { chromium } from "@playwright/test";

const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const title = `Playwright 업무 요청 ${Date.now()}`;

const created = await fetch(`${frontendUrl}/api/work-requests`, {
  body: JSON.stringify({ title, assignee_id: "jiho" }),
  headers: { "Content-Type": "application/json", "X-Demo-Persona": "mina" },
  method: "POST",
});
if (!created.ok) throw new Error(`work request setup failed: ${await created.text()}`);

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await page.selectOption("select", "jiho");
  await page.getByRole("button", { name: "판단", exact: true }).click();
  await page.getByText(title).waitFor();
  await page.getByRole("button", { name: "수락" }).last().click();
  await page.getByRole("button", { name: "내 업무", exact: true }).click();
  await page.getByText(title).waitFor();
  await page.getByText("열림").last().waitFor();
  console.log(JSON.stringify({ title, result: "accepted task projected to My Work" }));
} finally {
  await browser.close();
}
