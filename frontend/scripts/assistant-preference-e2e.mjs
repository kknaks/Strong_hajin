import { chromium } from "@playwright/test";

import { loginAs, switchAccount } from "./e2e-helpers.mjs";


const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

const characterKey = (scope, size) => scope.locator(`.assistant-character.${size}`).getAttribute("data-character-key");

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");

  const launcher = page.locator(".assistant-launcher");
  await launcher.waitFor();
  if (await characterKey(launcher, "launcher") !== "cream-cat") {
    throw new Error("a member without a saved preference did not receive the default character");
  }

  await page.getByRole("button", { name: "탐색 열기" }).click();
  await page.getByRole("button", { name: "설정" }).click();
  const picker = page.getByRole("dialog", { name: "내 AX 캐릭터" });
  await picker.waitFor();
  if (await picker.getByRole("radio").count() !== 9) throw new Error("picker did not expose all nine catalog choices");
  await page.screenshot({ path: "test-results/assistant-preference-picker-1280.png", fullPage: true });
  const saved = page.waitForResponse(
    (response) => response.url().endsWith("/api/profile/preferences/assistant-character")
      && response.request().method() === "PUT",
  );
  await picker.getByRole("radio", { name: /레서판다/ }).focus();
  await page.keyboard.press("Enter");
  if (!(await saved).ok()) throw new Error("assistant preference save failed");
  await picker.getByRole("radio", { name: /레서판다/ }).waitFor();
  if (await characterKey(launcher, "launcher") !== "red-panda") {
    throw new Error("picker choice did not synchronize to the launcher");
  }
  await picker.getByRole("button", { name: "캐릭터 선택 닫기" }).click();
  await page.locator(".rail-scrim").click();

  await page.getByRole("button", { name: "AX", exact: true }).click();
  const drawer = page.getByRole("complementary", { name: "AX 대화" });
  await drawer.waitFor();
  if (await characterKey(drawer, "header") !== "red-panda") {
    throw new Error("drawer header did not share the selected identity");
  }
  await drawer.getByRole("button", { name: "새 AX 대화" }).click();
  const composer = drawer.getByLabel("AX 메시지");
  await page.waitForFunction(() => document.querySelector("#ax-message")?.getAttribute("placeholder") === "메시지를 입력해 주세요.");
  await composer.fill("오늘 할 일을 간단히 정리해줘");
  await drawer.getByRole("button", { name: "보내기" }).click();
  await drawer.locator(".assistant.final").waitFor({ timeout: 120_000 });
  if (await drawer.locator(".assistant .assistant-character").count()) {
    throw new Error("selected character was repeated beside an answer");
  }
  await page.screenshot({ path: "test-results/assistant-preference-red-panda-1280.png", fullPage: true });
  await drawer.getByRole("button", { name: "닫기" }).click();

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("navigation", { name: "제품 탐색" }).waitFor();
  if (await characterKey(page.locator(".assistant-launcher"), "launcher") !== "red-panda") {
    throw new Error("reload lost the selected character");
  }

  await switchAccount(page, "jiho");
  if (await characterKey(page.locator(".assistant-launcher"), "launcher") !== "cream-cat") {
    throw new Error("another principal inherited Mina's character preference");
  }
  await switchAccount(page, "mina");
  if (await characterKey(page.locator(".assistant-launcher"), "launcher") !== "red-panda") {
    throw new Error("a new Mina session did not recover the saved preference");
  }

  await page.setViewportSize({ width: 720, height: 900 });
  await page.getByRole("button", { name: "탐색 열기" }).click();
  await page.getByRole("button", { name: "설정" }).click();
  const compactPicker = page.getByRole("dialog", { name: "내 AX 캐릭터" });
  const compactLayout = await compactPicker.locator(".assistant-character-grid").evaluate((grid) => ({
    columns: getComputedStyle(grid).gridTemplateColumns.split(" ").length,
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
  }));
  if (compactLayout.columns !== 3 || compactLayout.documentWidth > compactLayout.viewportWidth) {
    throw new Error(`720px picker layout is invalid: ${JSON.stringify(compactLayout)}`);
  }
  await page.screenshot({ path: "test-results/assistant-preference-picker-720.png", fullPage: true });

  console.log(JSON.stringify({
    result: "principal preference persisted and synchronized across launcher, picker, drawer, and answer profile",
    selected: "red-panda",
    catalog_size: 9,
    compact_columns: compactLayout.columns,
  }));
} finally {
  await browser.close();
}
