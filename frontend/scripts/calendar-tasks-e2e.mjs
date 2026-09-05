import { chromium } from "@playwright/test";

import { loginAs } from "./e2e-helpers.mjs";

// 업무의 기간을 달력에서 읽는다. Six date shapes, created through the ordinary commands, then read in the month and
// week grids: a range is one connected bar, a week boundary continues rather than restarting, a deadline-only task
// sits on its deadline alone, and work with no dates is not on the calendar at all.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();

// Each run picks its own week far enough ahead that two runs never crowd the same cells, and every expected
// column is computed from that week rather than written down.
const weeksAhead = 8 + (Math.floor(stamp / 1000) % 40);
const iso = (date) => date.toISOString().slice(0, 10);
const shift = (date, days) => new Date(date.getTime() + days * 86_400_000);
const todayUtc = new Date(`${new Date().toISOString().slice(0, 10)}T00:00:00Z`);
const target = shift(todayUtc, weeksAhead * 7);
// Sunday of that week: the grid's first column.
const weekStart = shift(target, -target.getUTCDay());
const day = (offset) => iso(shift(weekStart, offset));
const monthLabel = `${day(1).slice(0, 4)}/${day(1).slice(5, 7)}`;

const shapes = [
  { key: "range", title: `같은 주 기간 ${stamp}`, start_date: day(1), due_date: day(3) },
  { key: "crossing", title: `주를 넘는 기간 ${stamp}`, start_date: day(-2), due_date: day(1) },
  { key: "dueOnly", title: `기한만 ${stamp}`, due_date: day(4) },
  { key: "startOnly", title: `시작일만 ${stamp}`, start_date: day(8) },
  { key: "undated", title: `날짜 없음 ${stamp}` },
  { key: "overlap", title: `겹치는 기간 ${stamp}`, start_date: day(2), due_date: day(4) },
];

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

  const created = await page.evaluate(async (rows) => {
    const made = {};
    for (const row of rows) {
      const { key, ...body } = row;
      const response = await fetch("/api/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      made[key] = await response.json();
    }
    return made;
  }, shapes);

  // A start after a due date is refused by the command, not corrected by the calendar.
  const refused = await page.evaluate(async ({ title, startDate, dueDate }) => {
    const response = await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, start_date: startDate, due_date: dueDate }),
    });
    return response.status;
  }, { title: `거꾸로 된 기간 ${stamp}`, startDate: day(10), dueDate: day(2) });
  if (refused !== 422) throw new Error(`a backwards range was accepted: ${refused}`);

  await navigation.getByRole("button", { name: "캘린더" }).click();
  const grid = page.getByRole("grid", { name: "업무 캘린더" });
  await grid.waitFor({ timeout: 20_000 });
  // Walk to the month this run planned into.
  for (let step = 0; step < 24; step += 1) {
    const label = ((await page.locator(".calendar-toolbar b").first().textContent()) ?? "").trim();
    if (label.startsWith(monthLabel)) break;
    await page.getByRole("button", { name: "다음 달" }).click();
  }

  const bar = (title) => grid.getByRole("button", { name: new RegExp(title) });
  const geometry = async (title) =>
    bar(title).first().evaluate((node) => ({ column: node.style.gridColumn, label: node.getAttribute("aria-label"), classes: node.className }));

  const readable = (isoDate) => isoDate.replaceAll("-", "/");
  const range = await geometry(shapes[0].title);
  if (range.column !== "2 / span 3") throw new Error(`a same-week range is not one connected bar: ${JSON.stringify(range)}`);
  if (!range.label.includes(`${readable(day(1))} – ${readable(day(3))}`)) throw new Error(`the bar does not say what it covers: ${range.label}`);

  const crossing = await bar(shapes[1].title).count();
  if (crossing !== 2) throw new Error(`a range across a week boundary drew ${crossing} bars, expected one per week`);
  const parts = await bar(shapes[1].title).evaluateAll((nodes) => nodes.map((node) => ({ column: node.style.gridColumn, classes: node.className })));
  if (!parts.some((part) => part.classes.includes("continues-after")) || !parts.some((part) => part.classes.includes("continues-before"))) {
    throw new Error(`the crossing bar does not continue across the boundary: ${JSON.stringify(parts)}`);
  }

  const dueOnly = await geometry(shapes[2].title);
  if (dueOnly.column !== "5 / span 1") throw new Error(`a deadline-only task is not on its deadline alone: ${JSON.stringify(dueOnly)}`);
  if (!dueOnly.label.includes(`${readable(day(4))} 기한`)) throw new Error(`the deadline label is wrong: ${dueOnly.label}`);
  const startOnly = await geometry(shapes[3].title);
  if (!startOnly.label.includes(`${readable(day(8))} 시작`)) throw new Error(`the start-only label is wrong: ${startOnly.label}`);

  if (await grid.getByText(shapes[4].title).count()) throw new Error("work with no dates was placed on the calendar");

  // Overlapping ranges get their own lanes rather than sitting on top of each other.
  const lanes = await grid
    .getByRole("button", { name: new RegExp(`(${shapes[0].title}|${shapes[5].title})`) })
    .evaluateAll((nodes) => nodes.map((node) => node.style.gridRow));
  if (new Set(lanes).size !== 2) throw new Error(`overlapping ranges share a lane: ${JSON.stringify(lanes)}`);

  // Selecting a bar opens the canonical Task detail.
  await bar(shapes[0].title).first().click();
  const drawer = page.getByRole("dialog", { name: "업무 상세" });
  await drawer.waitFor({ timeout: 20_000 });
  if (!(await drawer.textContent())?.includes(shapes[0].title)) throw new Error("the calendar did not open the task it named");
  await drawer.getByRole("button", { name: "상세 닫기" }).click();

  // The week view reads the same projection.
  await page.getByRole("tab", { name: "주" }).click();
  await page.getByRole("grid", { name: "업무 캘린더" }).waitFor({ timeout: 20_000 });

  await page.screenshot({ path: "test-results/calendar-tasks-e2e.png", fullPage: true });
  console.log(JSON.stringify({ result: "task ranges read as connected bars, deadlines stand alone, undated work stays off the calendar", tasks: Object.fromEntries(Object.entries(created).map(([key, row]) => [key, row.task_id])) }));
} finally {
  await browser.close();
}
