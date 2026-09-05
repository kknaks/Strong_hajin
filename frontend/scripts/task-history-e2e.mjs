import { chromium } from "@playwright/test";

import { loginAs } from "./e2e-helpers.mjs";

// 이 업무가 어떻게 여기까지 왔는가 — in the browser, from the Task itself. Someone edits a Task, adds a step and
// starts it, then reads back who did what and opens the diff of one of those versions. Nothing below is computed
// in the page: every line and every diff comes from the server's authorized projection.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const first = `이력 업무 ${Date.now()}`;
const renamed = `${first} 고침`;

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

  const task = await page.evaluate(async (title) => {
    const created = await (await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, description: "처음 설명" }),
    })).json();
    return created;
  }, first);

  await page.reload({ waitUntil: "domcontentloaded" });
  await navigation.getByRole("button", { name: "내 업무" }).click();
  await page.getByRole("row", { name: new RegExp(first) }).click();
  const drawer = page.getByRole("dialog", { name: "업무 상세" });
  const checklist = drawer.locator('section[aria-label="체크리스트"]');
  await checklist.waitFor({ timeout: 20_000 });

  // A step is a change to the task, so the drawer must settle the version it moved to.
  const field = checklist.locator('input[id^="checklist-"]');
  await field.fill("자료 모으기");
  await field.press("Enter");
  await checklist.locator(".checklist-item", { hasText: "자료 모으기" }).waitFor({ timeout: 10_000 });

  // Renaming through the form right after that would 422 if the drawer were still holding the old version.
  const titleInput = drawer.locator("input.title-input");
  await titleInput.fill(renamed);
  await drawer.getByRole("button", { name: "변경 저장" }).click();
  // The list behind the drawer is the server's answer, so the rename landing there means the save went through.
  await page.getByRole("row", { name: new RegExp(renamed) }).waitFor({ timeout: 20_000 });

  const history = drawer.locator("section[aria-label='활동·이력']");
  await history.waitFor({ timeout: 20_000 });
  // The section sits at the end of a scrolling drawer: bring it fully into view the way a reader would.
  const openHistory = history.getByRole("button", { name: "이력 보기" });
  await openHistory.scrollIntoViewIfNeeded();
  await openHistory.click();
  const lines = history.locator("ol.activity-list > li");
  await lines.first().waitFor({ timeout: 20_000 });
  const count = await lines.count();
  if (count < 3) throw new Error(`history shows ${count} lines, expected the creation, the step and the edit`);

  const newest = ((await lines.first().textContent()) ?? "").replace(/\s+/g, " ");
  if (!newest.includes("민아")) throw new Error(`the newest line does not name who did it: ${JSON.stringify(newest)}`);
  if (!/\d{4}\/\d{2}\/\d{2} \d{2}:\d{2}/.test(newest)) throw new Error(`the newest line has no readable time: ${JSON.stringify(newest)}`);
  if (newest.includes("mina")) throw new Error(`a member id reached the screen: ${JSON.stringify(newest)}`);

  // The version the title edit produced is the newest one; its diff names the title and nothing that did not move.
  const editedVersion = Number(await lines.first().getAttribute("data-version"));
  if (!Number.isFinite(editedVersion) || editedVersion < 3) throw new Error(`unexpected newest version: ${editedVersion}`);
  await lines.first().getByRole("button", { name: "변경 내용" }).click();
  const diff = lines.first().locator("[aria-label='변경 내용']");
  await diff.waitFor({ timeout: 20_000 });
  const diffText = ((await diff.textContent()) ?? "").replace(/\s+/g, " ");
  if (!diffText.includes("제목") || !diffText.includes(first) || !diffText.includes(renamed)) {
    throw new Error(`the diff does not show the rename: ${JSON.stringify(diffText)}`);
  }
  if (diffText.includes("상태")) throw new Error(`the diff claims something moved that did not: ${JSON.stringify(diffText)}`);

  // The screen and the server agree, and history never widens who may read the task.
  const server = await page.evaluate(async (taskId) => {
    const authorized = await (await fetch(`/api/tasks/${taskId}/history`)).json();
    return { versions: authorized.versions.map((row) => row.version), events: authorized.activity.map((row) => row.event_kind) };
  }, task.task_id);
  if (server.versions.length < 3) throw new Error(`server froze ${server.versions.length} versions: ${JSON.stringify(server)}`);
  if (!server.events.includes("task.checklist.added")) throw new Error(`the step is missing from the ledger: ${JSON.stringify(server)}`);

  await page.screenshot({ path: "test-results/task-history-e2e.png", fullPage: true });
  console.log(JSON.stringify({ result: "task history read in the browser with actor, time and a real diff", task_id: task.task_id, lines: count, versions: server.versions }));
} finally {
  await browser.close();
}
