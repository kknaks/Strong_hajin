import { chromium } from "@playwright/test";

import { loginAs, pollFor, switchAccount } from "./e2e-helpers.mjs";

// 하위 업무는 체크리스트가 아니라 업무다 — broken out in the browser: a part gets its own holder, is accepted,
// worked and finished, and only then can the whole be finished. The parent shows progress, never a state of its own
// borrowed from the parts.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const parentTitle = `분기 마감 ${stamp}`;
const mineTitle = `매출 집계 ${stamp}`;
const theirsTitle = `비용 정리 ${stamp}`;

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "jiho");
  const navigation = page.getByRole("navigation", { name: "제품 탐색" });
  await navigation.getByRole("button", { name: "내 업무" }).click();

  const parent = await page.evaluate(async (title) => {
    const created = await (await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    })).json();
    await fetch(`/api/tasks/${created.task_id}/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: created.version }),
    });
    return created;
  }, parentTitle);

  // One part is made in the browser, from the parent's own detail.
  await page.reload({ waitUntil: "domcontentloaded" });
  await navigation.getByRole("button", { name: "내 업무" }).click();
  await page.getByRole("row", { name: new RegExp(parentTitle) }).click();
  const drawer = page.getByRole("dialog", { name: "업무 상세" });
  const subtasks = drawer.locator("section[aria-label='하위 업무']");
  await subtasks.waitFor({ timeout: 20_000 });
  await subtasks.getByRole("button", { name: "하위 업무 추가" }).click();
  await subtasks.getByLabel("하위 업무 제목").fill(mineTitle);
  await subtasks.getByRole("button", { name: "만들기" }).click();
  await subtasks.locator("li", { hasText: mineTitle }).waitFor({ timeout: 20_000 });

  // Another part is handed to someone else, who accepts it as the Task it is.
  const assigned = await page.evaluate(async ({ title, parentId }) => {
    return (await (await fetch("/api/tasks/assign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, assignee_id: "mina", parent_task_id: parentId }),
    })).json());
  }, { title: theirsTitle, parentId: parent.task_id });
  if (!assigned.task?.task_id) throw new Error(`assigning a part failed: ${JSON.stringify(assigned)}`);

  // The whole cannot be finished while its parts are not, and the refusal names one.
  const refused = await page.evaluate(async (taskId) => {
    const task = await (await fetch(`/api/tasks/${taskId}`)).json();
    const response = await fetch(`/api/tasks/${taskId}/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: task.version }),
    });
    return { status: response.status, body: await response.text(), progress: task.child_progress };
  }, parent.task_id);
  if (refused.status !== 422 || !refused.body.includes(mineTitle.slice(0, 6))) {
    throw new Error(`the whole was finished while a part was open: ${JSON.stringify(refused)}`);
  }
  if (refused.progress.done !== 0 || refused.progress.total !== 2) throw new Error(`unexpected progress: ${JSON.stringify(refused.progress)}`);

  // The other person accepts and finishes their part; from their side it names what it belongs to.
  await drawer.getByRole("button", { name: "상세 닫기" }).click();
  await page.getByRole("dialog").waitFor({ state: "detached", timeout: 20_000 });
  await switchAccount(page, "mina");
  const judgement = await pollFor(
    page,
    () =>
      page.evaluate(async (subject) => {
        const items = await (await fetch("/api/action-items")).json();
        return items.find((item) => item.subject === subject) ?? null;
      }, theirsTitle),
    { timeout: 20_000, description: "the assigned part to reach its holder" },
  );
  await page.evaluate(async ({ actionItemId, expectedVersion, taskId }) => {
    await fetch(`/api/action-items/${actionItemId}/commands/accept`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: expectedVersion }),
    });
    const task = await (await fetch(`/api/tasks/${taskId}`)).json();
    await fetch(`/api/tasks/${taskId}/start`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_version: task.version }) });
    const running = await (await fetch(`/api/tasks/${taskId}`)).json();
    await fetch(`/api/tasks/${taskId}/complete`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_version: running.version }) });
  }, { actionItemId: judgement.action_item_id, expectedVersion: judgement.expected_version, taskId: assigned.task.task_id });

  await page.reload({ waitUntil: "domcontentloaded" });
  await navigation.getByRole("button", { name: "내 업무" }).click();
  const filter = page.locator("#task-state-filter");
  await filter.waitFor();
  await filter.selectOption("all");
  await page.getByRole("row", { name: new RegExp(theirsTitle) }).click();
  const childDrawer = page.getByRole("dialog", { name: "업무 상세" });
  const belongs = childDrawer.locator("section[aria-label='상위 업무']");
  await belongs.waitFor({ timeout: 20_000 });
  if (!((await belongs.textContent()) ?? "").includes(parentTitle)) throw new Error("the part does not say what it belongs to");
  if (await childDrawer.locator("section[aria-label='하위 업무']").count()) throw new Error("a part was offered parts of its own");
  await childDrawer.getByRole("button", { name: "상세 닫기" }).click();

  // Back on the parent: one part done, one to go, and then the whole can be finished.
  await switchAccount(page, "jiho");
  const finished = await page.evaluate(async ({ parentId, mine }) => {
    const before = await (await fetch(`/api/tasks/${parentId}`)).json();
    const child = before.children.find((row) => row.title === mine);
    const childTask = await (await fetch(`/api/tasks/${child.task_id}`)).json();
    await fetch(`/api/tasks/${child.task_id}/start`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_version: childTask.version }) });
    const running = await (await fetch(`/api/tasks/${child.task_id}`)).json();
    await fetch(`/api/tasks/${child.task_id}/complete`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_version: running.version }) });
    const ready = await (await fetch(`/api/tasks/${parentId}`)).json();
    const closing = await fetch(`/api/tasks/${parentId}/complete`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_version: ready.version }) });
    const after = await (await fetch(`/api/tasks/${parentId}`)).json();
    return { progress: ready.child_progress, closing: closing.status, state: after.state };
  }, { parentId: parent.task_id, mine: mineTitle });
  if (finished.progress.done !== 2 || finished.closing !== 200 || finished.state !== "done") {
    throw new Error(`finishing the whole after its parts failed: ${JSON.stringify(finished)}`);
  }

  await page.screenshot({ path: "test-results/subtask-e2e.png", fullPage: true });
  console.log(JSON.stringify({ result: "work broken into parts, held separately, and only finished when its parts were", parent: parent.task_id, assigned_child: assigned.task.task_id }));
} finally {
  await browser.close();
}
