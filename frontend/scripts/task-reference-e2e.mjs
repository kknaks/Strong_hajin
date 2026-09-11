import { chromium } from "@playwright/test";

import { chooseOption, loginAs, pollFor, switchAccount } from "./e2e-helpers.mjs";

// 참고 업무: pointing at work that came before, in the browser. A requester picks earlier work while writing a
// request; the person who accepts gets the same pointer on the Task it becomes, and letting one go leaves the
// history able to say it was there.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const earlier = `지난 분기 보고 ${stamp}`;
const requested = `이번 분기 보고 ${stamp}`;

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

  const previous = await page.evaluate(async (title) => {
    return (await (await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    })).json());
  }, earlier);

  // Write the request in the browser and point it at the earlier work.
  await page.getByRole("button", { name: "새 업무 추가", exact: true }).click();
  await page.getByRole("tab", { name: "요청", exact: true }).click();
  await page.getByLabel("요청할 업무").fill(requested);
  await chooseOption(page, "담당 후보", /지호/);
  await page.getByRole("button", { name: "참고 업무 연결" }).click();
  await chooseOption(page, "연결할 이전 업무", earlier);
  await page.getByRole("button", { name: "연결", exact: true }).click();
  await page.getByRole("button", { name: "업무 요청 보내기" }).click();
  await page.getByRole("dialog").waitFor({ state: "detached", timeout: 20_000 });

  // Jiho accepts it through the judgement ledger.
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
  await page.evaluate(async ({ actionItemId, expectedVersion }) => {
    await fetch(`/api/action-items/${actionItemId}/commands/accept`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: expectedVersion }),
    });
  }, { actionItemId: judgement.action_item_id, expectedVersion: judgement.expected_version });

  // The pointer travelled, but the work it points at is not his to read.
  const derived = await page.evaluate(async (title) => {
    const rows = await (await fetch("/api/my-work")).json();
    const row = rows.find((task) => task.title === title);
    const detail = await (await fetch(`/api/tasks/${row.task_id}`)).json();
    return { task_id: row.task_id, references: detail.references };
  }, requested);
  if (derived.references.length !== 1) throw new Error(`the reference did not travel: ${JSON.stringify(derived.references)}`);
  if (derived.references[0].task !== null) throw new Error("the holder was shown work they may not read");

  // Back on the requester's side: the pointer resolves, opens the work, and can be let go of.
  await switchAccount(page, "mina");
  await navigation.getByRole("button", { name: "내 업무" }).click();
  await page.getByRole("button", { name: "새 업무 추가", exact: true }).click();
  await page.getByLabel("업무 제목").fill(`후속 정리 ${stamp}`);
  await page.getByRole("checkbox", { name: earlier }).check();
  await page.getByRole("button", { name: "업무 추가", exact: true }).click();
  await page.getByRole("dialog").waitFor({ state: "detached", timeout: 20_000 });

  await page.getByRole("row", { name: new RegExp(`후속 정리 ${stamp}`) }).click();
  const drawer = page.getByRole("dialog", { name: "업무 상세" });
  const references = drawer.locator("section[aria-label='참고 업무']");
  await references.waitFor({ timeout: 20_000 });
  await references.getByRole("button", { name: `${earlier} 열기` }).waitFor({ timeout: 20_000 });

  const followUp = await page.evaluate(async (title) => {
    const rows = await (await fetch("/api/my-work")).json();
    return rows.find((task) => task.title === title).task_id;
  }, `후속 정리 ${stamp}`);

  await references.getByRole("button", { name: `${earlier} 연결 해제` }).click();
  await page.waitForFunction(
    () => document.querySelector("section[aria-label='참고 업무']")?.textContent?.includes("연결된 업무가 없습니다"),
    undefined,
    { timeout: 20_000 },
  );

  // Letting go did not erase it: the version that had it still does, and the ledger says both things happened.
  const history = await page.evaluate(async (taskId) => {
    const answer = await (await fetch(`/api/tasks/${taskId}/history`)).json();
    return {
      events: answer.activity.map((row) => row.event_kind),
      hadReference: answer.versions.some((row) => (row.snapshot.references ?? []).length > 0),
      hasNow: (answer.versions[answer.versions.length - 1].snapshot.references ?? []).length,
    };
  }, followUp);
  if (!history.events.includes("task.reference_released")) throw new Error(`release is missing: ${JSON.stringify(history.events)}`);
  if (!history.hadReference || history.hasNow !== 0) throw new Error(`history does not restore the connection: ${JSON.stringify(history)}`);

  await page.screenshot({ path: "test-results/task-reference-e2e.png", fullPage: true });
  console.log(JSON.stringify({ result: "a reference travelled with a request, opened, and was released without erasing it", earlier: previous.task_id, derived: derived.task_id, follow_up: followUp }));
} finally {
  await browser.close();
}
