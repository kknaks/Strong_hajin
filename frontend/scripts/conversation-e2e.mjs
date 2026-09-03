import { chromium } from "@playwright/test";

const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";

const browser = await chromium.launch({
  executablePath:
    process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "AX" }).click();
  const newConversation = page.getByRole("button", { name: "새 AX 대화" });
  await newConversation.click();
  await page
    .getByLabel("AX 메시지")
    .fill("SCAX MCP의 task_list를 사용해 첫 번째 대화의 내 업무 수만 알려줘.");
  await page.getByRole("button", { name: "보내기" }).click();
  await page.getByRole("button", { name: "대기열에 보내기" }).waitFor({ timeout: 20_000 });
  await page
    .getByLabel("AX 메시지")
    .fill("첫 번째 대화의 두 번째 발화입니다. 같은 task_list를 다시 확인해줘.");
  await page.getByRole("button", { name: "대기열에 보내기" }).click();
  await page.getByText("대기 중").waitFor({ timeout: 20_000 });

  await newConversation.click();
  await page
    .getByLabel("AX 메시지")
    .fill("SCAX MCP의 task_list를 사용해 두 번째 대화의 내 업무 수만 알려줘.");
  await page.getByRole("button", { name: "보내기" }).click();

  const conversations = page.locator(".ax-conversation-list button");
  await conversations.nth(1).click();
  await page.getByText("첫 번째 대화의 두 번째 발화입니다.").waitFor({ timeout: 20_000 });
  await page.getByText("두 번째 대화의 내 업무 수만 알려줘.").count().then((count) => {
    if (count !== 0) throw new Error("Conversation state leaked across the active-session switch");
  });
  await conversations.nth(0).click();
  await page.getByText("task list · completed").waitFor({ timeout: 90_000 });
  await conversations.nth(1).click();
  await page.waitForFunction(
    () => [...document.querySelectorAll("details summary")].filter((item) => item.textContent === "task list · completed").length >= 2,
    undefined,
    { timeout: 90_000 },
  );
  await page.screenshot({ path: "test-results/conversation-e2e.png", fullPage: true });
  console.log(
    JSON.stringify({
      result: "persona-bound task_list completed with queued follow-up and independent conversation",
    }),
  );
} finally {
  await browser.close();
}
