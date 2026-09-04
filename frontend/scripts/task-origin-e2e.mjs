import { chromium } from "@playwright/test";

import { loginAs, pollFor, switchAccount } from "./e2e-helpers.mjs";

// Who asked for this work, seen from each side, after a real reload. The requester, the assigner and the assignee
// each read the same server answer; nothing is reconstructed from a list the browser happens to be holding.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const requested = `요청 출처 업무 ${stamp}`;
const assigned = `배정 출처 업무 ${stamp}`;

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

const openTask = async (page, title) => {
  await page.getByRole("row", { name: new RegExp(title) }).click();
  return page.getByRole("dialog", { name: "업무 상세" });
};

try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");
  const navigation = page.getByRole("navigation", { name: "제품 탐색" });
  await navigation.getByRole("button", { name: "내 업무" }).click();

  // Mina asks Jiho for work; Jiho accepts it through the judgement ledger.
  await page.getByRole("button", { name: "새 업무 추가" }).click();
  await page.getByRole("tab", { name: "요청", exact: true }).click();
  await page.getByLabel("요청할 업무").fill(requested);
  await page.getByLabel("담당 후보").selectOption("jiho");
  await page.getByRole("button", { name: "업무 요청 보내기" }).click();

  await switchAccount(page, "jiho");
  const judgement = await pollFor(
    page,
    () =>
      page.evaluate(async (subject) => {
        const items = await (await fetch("/api/action-items")).json();
        return items.find((item) => item.subject === subject) ?? null;
      }, requested),
    { timeout: 20_000, description: "the request to reach the reviewer" },
  );
  await navigation.getByRole("button", { name: "내 업무" }).click();
  await page.locator(`.decision-panel .task-card[data-action-item-id="${judgement.action_item_id}"]`).getByRole("button", { name: "판단하기" }).click();
  await page.getByRole("dialog", { name: "판단 상세" }).getByRole("button", { name: "수락" }).click();
  await page.getByRole("dialog").waitFor({ state: "detached", timeout: 20_000 });

  // Jiho also assigns himself-created work to Mina, so both origins exist side by side.
  const assignment = await page.evaluate(async (title) => {
    const created = await (await fetch("/api/tasks/assign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, assignee_id: "mina" }),
    })).json();
    return created;
  }, assigned);

  // A full reload: nothing below can come from memory.
  await page.reload({ waitUntil: "domcontentloaded" });
  await navigation.getByRole("button", { name: "내 업무" }).click();
  const assigneeDrawer = await openTask(page, requested);
  const requesterRow = assigneeDrawer.locator(".meta-grid div", { hasText: "요청자" }).first();
  await requesterRow.waitFor({ timeout: 20_000 });
  const requesterText = ((await requesterRow.textContent()) ?? "").replace(/\s+/g, " ");
  if (!requesterText.includes("요청자") || !requesterText.includes("민아")) {
    throw new Error(`assignee does not see the requester: ${JSON.stringify(requesterText)}`);
  }
  if (requesterText.includes("지호")) throw new Error("the assignee's own name is reported as the origin actor");
  await assigneeDrawer.getByRole("button", { name: "상세 닫기" }).click();

  // The requester navigates the other way, in the browser: request → derived Task, read-only.
  await switchAccount(page, "mina");
  await navigation.getByRole("button", { name: "내 업무" }).click();
  await page.getByRole("tab", { name: "요청·배정" }).click();
  await page
    .locator("section[aria-label='내가 요청한 업무']")
    .locator("tr", { hasText: requested })
    .getByRole("button", { name: "상세보기" })
    .click();
  const requestDrawer = page.getByRole("dialog", { name: "업무 요청 상세" });
  await requestDrawer.getByRole("button", { name: "파생 업무 보기" }).click();
  const readOnlyTask = page.getByRole("dialog", { name: "업무 상세" });
  await readOnlyTask.waitFor({ timeout: 20_000 });
  if (!(await readOnlyTask.textContent())?.includes(requested)) throw new Error("the requester did not open the derived task");
  // Reading is not holding: the workspace is absent and the title cannot be edited.
  if (await readOnlyTask.locator("section[aria-label='체크리스트']").count()) throw new Error("a read-only viewer was given the holder's checklist");
  if (await readOnlyTask.getByText("참고 자료").count()) throw new Error("a read-only viewer was given the holder's materials");
  if (!(await readOnlyTask.locator("input.title-input").isDisabled())) throw new Error("a read-only viewer can edit the title");

  // And back again: the allowed source returns to the request it came from.
  await readOnlyTask.getByRole("button", { name: requested }).click();
  await page.getByRole("dialog", { name: "업무 요청 상세" }).waitFor({ timeout: 20_000 });
  await page.getByRole("dialog", { name: "업무 요청 상세" }).getByRole("button", { name: "상세 닫기" }).click();

  // The assigner reads their own side without holding the work either.
  await switchAccount(page, "jiho");
  const assignerView = await page.evaluate(async (title) => {
    const rows = await (await fetch("/api/work-requests")).json();
    const request = rows.find((row) => row.title === title);
    if (!request?.task_id) return null;
    const task = await (await fetch(`/api/tasks/${request.task_id}`)).json();
    return { access: task.access, role: task.origin?.actor_role, actor: task.origin?.actor?.member_id, source: task.origin?.source?.id ?? null };
  }, requested);
  if (assignerView?.access !== "owner" || assignerView.role !== "요청자" || assignerView.actor !== "mina") {
    throw new Error(`the assignee's own view is wrong: ${JSON.stringify(assignerView)}`);
  }

  await switchAccount(page, "mina");
  await navigation.getByRole("button", { name: "내 업무" }).click();

  // The assignee of a direct assignment sees the assigner, never themselves.
  await navigation.getByRole("button", { name: "내 업무" }).click();
  await page.evaluate(async (assignmentId) => {
    await fetch(`/api/task-assignments/${assignmentId}/accept`, { method: "POST" });
  }, assignment.assignment_id);
  await page.reload({ waitUntil: "domcontentloaded" });
  await navigation.getByRole("button", { name: "내 업무" }).click();
  const assignedDrawer = await openTask(page, assigned);
  const assignerRow = assignedDrawer.locator(".meta-grid div", { hasText: "배정자" }).first();
  await assignerRow.waitFor({ timeout: 20_000 });
  const assignerText = ((await assignerRow.textContent()) ?? "").replace(/\s+/g, " ");
  if (!assignerText.includes("배정자") || !assignerText.includes("지호")) {
    throw new Error(`assignee does not see the assigner: ${JSON.stringify(assignerText)}`);
  }
  if (assignerText.includes("민아")) throw new Error("the assignee is reported as the assigner");
  await assignedDrawer.getByRole("button", { name: "상세 닫기" }).click();

  // A self-created task names its creator, and the whole answer comes from the server.
  const origins = await page.evaluate(async () => {
    const rows = await (await fetch("/api/my-work")).json();
    return rows.map((row) => ({ title: row.title, kind: row.origin?.kind, role: row.origin?.actor_role, actor: row.origin?.actor?.member_id }));
  });
  const assignedOrigin = origins.find((row) => row.title === assigned);
  if (assignedOrigin?.kind !== "direct_assignment" || assignedOrigin.role !== "배정자" || assignedOrigin.actor !== "jiho") {
    throw new Error(`list projection disagrees with the detail: ${JSON.stringify(assignedOrigin)}`);
  }

  await page.screenshot({ path: "test-results/task-origin-e2e.png", fullPage: true });
  console.log(
    JSON.stringify({
      result: "requester, assigner and assignee read one origin, and navigation works both ways",
      assigner_view: assignerView,
      assigned_origin: assignedOrigin,
    }),
  );
} finally {
  await browser.close();
}
