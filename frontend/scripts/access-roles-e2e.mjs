import { chromium } from "@playwright/test";

import { chooseOption, loginAs, pollFor, quickLoginAs, switchAccount } from "./e2e-helpers.mjs";

/**
 * 같은 원장, 다른 범위: 대표 · 팀장 · 구성원이 각자 볼 수 있는 것만 본다.
 *
 * Everyone signs in the ordinary way, with an address and a password. What they then see is not a property of the
 * login: it is read from the roles and scoped grants in the database. 대표 reads the organization's work and its
 * private meetings; 팀장's authority stops at their own team; a 구성원 sees their own work and nothing else.
 */
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const minaTask = `민아가 들고 있는 일 ${stamp}`;
const privateMeeting = `비공개 회의 ${stamp}`;

const browser = await chromium.launch({
  executablePath:
    process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });

  // 구성원: 자기 업무를 만들고, 자기 비공개 회의를 연다.
  await loginAs(page, "mina");
  const created = await page.evaluate(
    async ({ title, meetingTitle }) => {
      const task = await (
        await fetch("/api/tasks", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title }),
        })
      ).json();
      const starts = new Date(Date.now() + 3_600_000);
      const meeting = await (
        await fetch("/api/meetings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            organization_id: "scax",
            title: meetingTitle,
            starts_at: starts.toISOString(),
            ends_at: new Date(starts.getTime() + 1_800_000).toISOString(),
            visibility: "private",
            attendee_ids: [],
          }),
        })
      ).json();
      return { task, meeting };
    },
    { title: minaTask, meetingTitle: privateMeeting },
  );
  if (!created.task?.task_id || !created.meeting?.meeting_id) throw new Error(`setup failed: ${JSON.stringify(created)}`);

  await page.getByRole("navigation", { name: "제품 탐색" }).getByRole("button", { name: "업무" }).click();
  await page.getByRole("row", { name: new RegExp(minaTask) }).first().waitFor();
  // A 구성원 has no organization-wide read, so that tab is not offered to them at all.
  if ((await page.getByRole("tab", { name: "조직 업무" }).count()) !== 0) {
    throw new Error("a member was offered the organization-wide work tab");
  }

  // 팀장: 제품팀을 이끌지만 전체 조회 권한은 없다 — 민아가 혼자 들고 있는 업무는 보이지 않는다.
  await switchAccount(page, "jiho");
  await page.getByRole("navigation", { name: "제품 탐색" }).getByRole("button", { name: "업무" }).click();
  if ((await page.getByRole("tab", { name: "조직 업무" }).count()) !== 0) {
    throw new Error("a team lead was offered the organization-wide work tab");
  }
  const leadSees = await page.evaluate(async (taskId) => (await fetch(`/api/tasks/${taskId}`)).status, created.task.task_id);
  if (leadSees !== 404) throw new Error(`a team lead could read another person's task: ${leadSees}`);
  const leadMeeting = await page.evaluate(async (meetingId) => (await fetch(`/api/meetings/${meetingId}`)).status, created.meeting.meeting_id);
  if (leadMeeting === 200) throw new Error("a team lead could read a private meeting they were not part of");

  // 대표: 조직의 업무를 읽는다 — 읽기까지만. 로컬 데모 계정 버튼 한 번으로 들어가되, 지나가는 길은 진짜 로그인이다.
  await page.getByRole("button", { name: "로그아웃" }).click();
  const signedIn = page.waitForResponse(
    (response) => response.url().endsWith("/api/auth/login") && response.request().method() === "POST",
  );
  await quickLoginAs(page, "yuna");
  const loginResponse = await signedIn;
  if (loginResponse.status() !== 200 || (await loginResponse.json()).member_id !== "yuna") {
    throw new Error(`the shortcut did not sign in through the login route: ${loginResponse.status()}`);
  }
  await page.getByRole("navigation", { name: "제품 탐색" }).getByRole("button", { name: "업무" }).click();
  await page.getByRole("tab", { name: "조직 업무" }).click();
  const row = page.locator(`[data-organization-task="${created.task.task_id}"]`);
  await pollFor(page, async () => (await row.count()) > 0, { timeout: 20_000, description: "민아의 업무가 조직 업무에 보이는 것" });
  if (!(await row.textContent())?.includes("민아")) throw new Error("the organization list did not name the holder");

  // 할일에는 남의 업무가 섞이지 않는다.
  await page.getByRole("tab", { name: "할일" }).click();
  if ((await page.getByRole("row", { name: new RegExp(minaTask) }).count()) !== 0) {
    throw new Error("someone else's work appeared in 할일");
  }

  const executive = await page.evaluate(
    async ({ taskId, meetingId, version }) => {
      const detail = await (await fetch(`/api/tasks/${taskId}`)).json();
      const meeting = await fetch(`/api/meetings/${meetingId}`);
      const moved = await fetch(`/api/tasks/${taskId}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_version: version }),
      });
      const mine = await (await fetch("/api/my-work")).json();
      return { access: detail.access, private_meeting_status: meeting.status, moved: moved.status, mine: mine.map((task) => task.task_id) };
    },
    { taskId: created.task.task_id, meetingId: created.meeting.meeting_id, version: created.task.version },
  );
  if (executive.access !== "read_only") throw new Error(`the executive's read was not read-only: ${JSON.stringify(executive)}`);
  if (executive.private_meeting_status !== 200) throw new Error(`the executive could not read the private meeting: ${executive.private_meeting_status}`);
  if (executive.moved === 200) throw new Error("reading the organization's work let the executive drive it");
  if (executive.mine.includes(created.task.task_id)) throw new Error("someone else's work entered the executive's 내 업무");

  // 관리자 화면: 대표가 조직 화면에서 민아의 권한을 넓히고, 그 자리에서 다시 회수한다.
  await page.getByRole("navigation", { name: "제품 탐색" }).getByRole("button", { name: "조직" }).click();
  await page.getByRole("region", { name: "조직 tree" }).getByRole("button", { name: /^제품팀/ }).click();
  await page.getByRole("region", { name: "제품팀 구성원" }).getByRole("button", { name: /민아/ }).click();
  await page.getByRole("region", { name: "민아 상세" }).waitFor();
  const grantCount = () => page.evaluate(async () => {
    const member = await (await fetch("/api/organization/members/mina")).json();
    return member.grants.length;
  });
  const before = await grantCount();
  await page.getByRole("button", { name: "변경" }).click();
  let access = page.getByRole("dialog", { name: "권한 변경" });
  await access.waitFor();
  await chooseOption(access, "역할", "팀장");
  await chooseOption(access, "범위", "제품팀");
  await access.getByLabel("사유").fill("팀장 대행");
  await access.getByRole("button", { name: "권한 부여" }).click();
  await pollFor(page, async () => (await grantCount()) === before + 1, {
    timeout: 15_000,
    description: "부여한 권한이 원장에 나타나는 것",
  });
  await page.getByRole("button", { name: "변경" }).click();
  access = page.getByRole("dialog", { name: "권한 변경" });
  await access.getByLabel("사유").fill("팀장 대행 종료");
  const added = access.locator("[data-grant]", { hasText: "팀장" }).last();
  await added.getByRole("button", { name: "회수" }).click();
  await page.getByRole("alertdialog", { name: "이 권한을 회수할까요?" }).getByRole("button", { name: "권한 회수" }).click();
  await pollFor(
    page,
    async () => {
      const now = await grantCount();
      return now === before ? { now } : null;
    },
    { timeout: 15_000, description: `회수한 권한이 원장에서 사라지는 것 (부여 전 ${before}개)` },
  );

  console.log(JSON.stringify({ task: created.task.task_id, meeting: created.meeting.meeting_id, ...executive }, null, 2));
  console.log("access roles e2e passed");
} finally {
  await browser.close();
}
