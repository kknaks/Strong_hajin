import { chromium } from "@playwright/test";

import { loginAs } from "./e2e-helpers.mjs";

// The Task detail as a person actually meets it: on a normal laptop viewport, without scrolling, the checklist and
// its add control must be visible, and every date on screen must read YYYY/MM/DD whatever the browser locale is.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const title = `상세 레이아웃 업무 ${Date.now()}`;

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  // A US-English browser is the case that used to render mm/dd/yyyy.
  const context = await browser.newContext({ viewport: { width: 1678, height: 970 }, locale: "en-US" });
  const page = await context.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");
  const navigation = page.getByRole("navigation", { name: "제품 탐색" });
  await navigation.getByRole("button", { name: "내 업무" }).click();

  const task = await page.evaluate(async (taskTitle) => {
    const created = await (await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: taskTitle, description: "레이아웃 회귀용 업무 내용입니다.\n".repeat(12), due_date: "2026-09-30" }),
    })).json();
    for (const text of ["자료 모으기", "초안 쓰기", "검토 요청"]) {
      await fetch(`/api/tasks/${created.task_id}/checklist`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    }
    const items = (await (await fetch(`/api/tasks/${created.task_id}`)).json()).checklist;
    await fetch(`/api/tasks/${created.task_id}/checklist/${items[0].item_id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ done: true }) });
    return created;
  }, title);

  await page.reload({ waitUntil: "domcontentloaded" });
  await navigation.getByRole("button", { name: "내 업무" }).click();

  // The list carries a compact cue before anything is opened.
  const row = page.getByRole("row", { name: new RegExp(title) });
  await row.waitFor({ timeout: 20_000 });
  const cue = (await row.locator(".checklist-cue").textContent())?.trim();
  if (cue !== "☐ 1/3") throw new Error(`list cue is wrong: ${JSON.stringify(cue)}`);

  await row.click();
  const drawer = page.getByRole("dialog", { name: "업무 상세" });
  const checklist = drawer.locator('section[aria-label="체크리스트"]');
  await checklist.waitFor({ timeout: 20_000 });

  // First screen: the section, its progress and its add control are all inside the viewport without scrolling.
  const viewport = page.viewportSize();
  const probes = {
    section: checklist,
    progress: checklist.locator(".checklist-progress"),
    firstItem: checklist.locator(".checklist-item").first(),
    addField: checklist.locator('input[id^="checklist-"]'),
    addButton: checklist.getByRole("button", { name: "추가" }),
  };
  const offscreen = [];
  for (const [name, locator] of Object.entries(probes)) {
    const box = await locator.boundingBox();
    if (!box) {
      offscreen.push(`${name}: not rendered`);
      continue;
    }
    if (box.y < 0 || box.y + box.height > viewport.height) {
      offscreen.push(`${name}: y=${Math.round(box.y)} h=${Math.round(box.height)} viewport=${viewport.height}`);
    }
  }
  if (offscreen.length > 0) throw new Error(`checklist is not on the first screen — ${offscreen.join("; ")}`);
  const progressText = (await probes.progress.textContent())?.trim();
  if (progressText !== "1/3") throw new Error(`progress chip is wrong: ${JSON.stringify(progressText)}`);

  // Dates on screen are ours, not the browser's.
  const dueField = drawer.locator(`#task-due-${task.task_id}`);
  if ((await dueField.inputValue()) !== "2026/09/30") throw new Error(`due date shows ${await dueField.inputValue()}`);
  if ((await dueField.getAttribute("type")) !== "text") throw new Error("the visible date control is still a native date input");
  const nativePlaceholders = await drawer.evaluate((node) =>
    Array.from(node.querySelectorAll('input[type="date"]')).map((input) => ({ hidden: input.classList.contains("sr-only"), value: input.value })),
  );
  if (nativePlaceholders.some((entry) => !entry.hidden)) throw new Error("a native date input is still visible");
  const drawerText = (await drawer.textContent()) ?? "";
  if (/mm\/dd\/yyyy/i.test(drawerText)) throw new Error("the browser locale date format is still on screen");
  if (/\d{4}-\d{2}-\d{2}/.test(drawerText)) throw new Error("a raw ISO date is rendered on screen");

  // The calendar is still reachable, and the value crossing the API stays ISO.
  await drawer.getByRole("button", { name: "기한 달력 열기" }).waitFor();
  await dueField.fill("2026/10/15");
  const saved = await page.evaluate(async ({ taskId, version }) => {
    const response = await fetch(`/api/tasks/${taskId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_version: version, due_date: "2026-10-15" }) });
    return { status: response.status, body: await response.json() };
  }, { taskId: task.task_id, version: task.version });
  if (saved.status !== 200 || saved.body.due_date !== "2026-10-15") throw new Error(`API boundary is not ISO: ${JSON.stringify(saved)}`);

  await page.screenshot({ path: "test-results/task-detail-layout-e2e.png" });
  console.log(JSON.stringify({ result: "task detail shows the checklist on the first screen with YYYY/MM/DD dates", task_id: task.task_id, viewport, list_cue: cue, progress: progressText }));
} finally {
  await browser.close();
}
