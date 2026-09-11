import { chromium } from "@playwright/test";

import { loginAs } from "./e2e-helpers.mjs";

const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const projectName = `참여 이력 검증 ${Date.now()}`;
const reason = "고객사 지원 종료";

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

async function openProjects(page) {
  const projectButton = page.getByRole("navigation", { name: "제품 탐색" }).getByRole("button", { name: "프로젝트" });
  await projectButton.focus();
  await page.keyboard.press("Enter");
  await page.getByRole("region", { name: "프로젝트" }).waitFor();
}

async function logOut(page) {
  await page.evaluate(() => fetch("/api/auth/logout", { method: "POST" }));
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await page.getByLabel("이메일").waitFor();
}

async function selectProject(page) {
  await openProjects(page);
  await page.getByRole("button", { name: projectName }).click();
  await page.getByText(projectName, { exact: true }).last().waitFor();
}

async function attachHyeon(page, kind) {
  const current = page.getByRole("region", { name: "현재 참여자" });
  await current.getByLabel("붙일 구성원").click();
  await page.getByRole("option", { name: "현우" }).click();
  await current.getByRole("button", { name: kind === "lead" ? "담당으로 붙이기" : "참여로 붙이기" }).click();
  await current.getByRole("button", { name: "현우 참여 종료" }).waitFor();
}

try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "jiho");
  await openProjects(page);

  await page.getByRole("button", { name: "새 프로젝트" }).click();
  await page.getByLabel("이름").fill(projectName);
  await page.getByRole("button", { name: "열기", exact: true }).click();
  await page.getByText(projectName, { exact: true }).last().waitFor();
  await attachHyeon(page, "member");

  await logOut(page);
  await loginAs(page, "hyeon");
  await selectProject(page);

  await logOut(page);
  await loginAs(page, "jiho");
  await selectProject(page);
  const current = page.getByRole("region", { name: "현재 참여자" });
  await current.getByRole("button", { name: "현우 참여 종료" }).click();
  await page.getByLabel("참여 종료 사유 (선택)").fill(reason);
  await page.getByRole("button", { name: "종료 기록" }).click();
  await current.getByRole("button", { name: "현우 참여 종료" }).waitFor({ state: "detached" });
  const history = page.getByRole("region", { name: "참여 이력" });
  await history.getByText(reason, { exact: true }).waitFor();
  if (((await history.textContent()) ?? "").includes("처리 현우")) {
    throw new Error("the release actor was inferred from the participant instead of the manager");
  }

  await logOut(page);
  await loginAs(page, "hyeon");
  await openProjects(page);
  if (await page.getByRole("button", { name: projectName }).count()) {
    throw new Error("the released participant still sees the project");
  }

  await logOut(page);
  await loginAs(page, "jiho");
  await selectProject(page);
  await attachHyeon(page, "lead");
  const hyeonHistory = page.getByRole("region", { name: "참여 이력" }).locator('li[data-assignment-id]').filter({ hasText: "현우" });
  if ((await hyeonHistory.count()) !== 2) {
    throw new Error(`rejoining did not preserve two participation rounds: ${await hyeonHistory.count()}`);
  }

  await logOut(page);
  await loginAs(page, "hyeon");
  await selectProject(page);

  console.log(JSON.stringify({
    result: "participation → release/current access loss/history preservation → rejoin verified through the UI",
    project: projectName,
  }));
} finally {
  await browser.close();
}
