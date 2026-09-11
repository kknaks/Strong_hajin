import { chromium } from "@playwright/test";

import { loginAs, switchAccount } from "./e2e-helpers.mjs";

// A Task's checklist through the real UI: add steps, check one off, remove one, and confirm it survives a re-open
// and never leaks into another person's view or the judgement ledger.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const title = `체크리스트 업무 ${Date.now()}`;

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

  const task = await page.evaluate(async (taskTitle) => {
    const response = await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: taskTitle, description: "체크리스트 acceptance" }),
    });
    return response.json();
  }, title);

  await page.reload({ waitUntil: "domcontentloaded" });
  await navigation.getByRole("button", { name: "내 업무" }).click();
  await page.getByRole("row", { name: new RegExp(title) }).click();
  const drawer = page.getByRole("dialog", { name: "업무 상세" });
  const checklist = drawer.locator('section[aria-label="체크리스트"]');
  await checklist.waitFor({ timeout: 20_000 });
  await checklist.getByText("아직 단계가 없습니다.", { exact: false }).waitFor();

  // Add three steps through the field, one with Enter and the rest with the button.
  const field = checklist.locator('input[id^="checklist-"]');
  await field.fill("자료 모으기");
  await field.press("Enter");
  // Enter returns after the key event, not after the request. Wait for the first
  // row so the intentional in-flight duplicate guard does not swallow the next
  // distinct step on a loaded acceptance machine.
  await checklist.locator(".checklist-item", { hasText: "자료 모으기" }).waitFor({ timeout: 10_000 });
  for (const step of ["초안 쓰기", "검토 요청"]) {
    await field.fill(step);
    await checklist.getByRole("button", { name: "추가" }).click();
    // Let the list settle before the next add, so the row nodes are not replaced under the next action.
    await checklist.locator(".checklist-item", { hasText: step }).waitFor({ timeout: 10_000 });
  }
  await checklist.locator(".checklist-progress[data-done='0'][data-total='3']").waitFor({ timeout: 10_000 });
  const texts = await checklist.locator(".checklist-item .checkbox-label").allTextContents();
  if (texts.join("|") !== "자료 모으기|초안 쓰기|검토 요청") throw new Error(`unexpected order: ${JSON.stringify(texts)}`);

  // Check one off; the progress and the strike-through follow the server's answer.
  // The box is controlled by the server's answer, so it flips only after the PATCH lands.
  const secondStep = checklist.getByRole("checkbox", { name: "초안 쓰기" });
  await secondStep.waitFor({ state: "visible", timeout: 10_000 });
  await secondStep.click();
  await checklist.locator(".checklist-progress[data-done='1'][data-total='3']").waitFor({ timeout: 10_000 });
  if (!(await secondStep.isChecked())) throw new Error("the checkbox did not follow the server's answer");
  const doneCount = await checklist.locator(".checklist-item.done").count();
  if (doneCount !== 1) throw new Error(`expected one finished step, found ${doneCount}`);

  // Rename a step in place; the version the server answers is what the next change carries.
  await checklist.getByRole("button", { name: "초안 쓰기 수정" }).click();
  const editor = checklist.locator("input.step-edit");
  await editor.fill("초안 다시 쓰기");
  await editor.press("Enter");
  await checklist.locator(".checklist-item", { hasText: "초안 다시 쓰기" }).waitFor({ timeout: 10_000 });

  // Move it with the keyboard-reachable control, and the whole order is what the server stores.
  await checklist.getByRole("button", { name: "초안 다시 쓰기 위로" }).click();
  await page.waitForFunction(
    () => {
      const rows = Array.from(document.querySelectorAll('section[aria-label="체크리스트"] .checklist-item .checkbox-label'));
      return rows.map((node) => node.textContent).join("|") === "초안 다시 쓰기|자료 모으기|검토 요청";
    },
    undefined,
    { timeout: 10_000 },
  );
  const storedOrder = await page.evaluate(async (taskId) => {
    const mine = await (await fetch(`/api/tasks/${taskId}`)).json();
    return mine.checklist.map((row) => `${row.position}:${row.text}`);
  }, task.task_id);
  if (storedOrder.join("|") !== "1:초안 다시 쓰기|2:자료 모으기|3:검토 요청") {
    throw new Error(`the server stored a different order: ${JSON.stringify(storedOrder)}`);
  }

  // Take one off the list, then confirm the state survives closing and re-opening the drawer.
  await checklist.getByRole("button", { name: "검토 요청 삭제" }).click();
  await checklist.locator(".checklist-progress[data-done='1'][data-total='2']").waitFor({ timeout: 10_000 });
  await drawer.getByRole("button", { name: "상세 닫기" }).click();
  await page.getByRole("row", { name: new RegExp(title) }).click();
  const reopened = page.getByRole("dialog", { name: "업무 상세" }).locator('section[aria-label="체크리스트"]');
  await reopened.locator(".checklist-progress[data-done='1'][data-total='2']").waitFor({ timeout: 20_000 });
  const after = await reopened.locator(".checklist-item .checkbox-label").allTextContents();
  if (after.join("|") !== "초안 다시 쓰기|자료 모으기") throw new Error(`checklist did not survive re-open: ${JSON.stringify(after)}`);

  // The step that left the list is still in what the Task was, marked as archived rather than erased.
  const archived = await page.evaluate(async (taskId) => {
    const history = await (await fetch(`/api/tasks/${taskId}/history`)).json();
    const latest = history.versions[history.versions.length - 1].snapshot.checklist;
    return { states: latest.map((row) => `${row.text}:${row.state}`), events: history.activity.map((row) => row.event_kind) };
  }, task.task_id);
  if (!archived.states.includes("검토 요청:archived")) throw new Error(`the archived step is missing: ${JSON.stringify(archived.states)}`);
  if (!archived.events.includes("task.checklist.reordered")) throw new Error(`the reorder left no record: ${JSON.stringify(archived.events)}`);

  // The steps are the Task's own: they are not judgements and not visible to someone without the Task.
  const ledger = await page.evaluate(async () => (await (await fetch("/api/action-items")).json()).length);
  const stored = await page.evaluate(async (taskId) => {
    const mine = await (await fetch(`/api/tasks/${taskId}`)).json();
    return { progress: mine.checklist_progress, items: mine.checklist.map((row) => row.text) };
  }, task.task_id);
  if (stored.progress.done !== 1 || stored.progress.total !== 2) throw new Error(`server progress mismatch: ${JSON.stringify(stored.progress)}`);

  await page.screenshot({ path: "test-results/task-checklist-e2e.png", fullPage: true });

  // Really sign in as someone else: the session decides who is asking, so a header cannot fake it.
  await page.getByRole("dialog", { name: "업무 상세" }).getByRole("button", { name: "상세 닫기" }).click();
  await page.getByRole("dialog").waitFor({ state: "detached", timeout: 10_000 });
  await switchAccount(page, "jiho");
  const otherStatus = await page.evaluate(async (taskId) => (await fetch(`/api/tasks/${taskId}`)).status, task.task_id);
  if (otherStatus !== 404) throw new Error(`another member could read the task: ${otherStatus}`);

  console.log(JSON.stringify({ result: "task checklist added, checked, renamed, reordered, archived and restored", task_id: task.task_id, ...stored, pending_judgements: ledger }));
} finally {
  await browser.close();
}
