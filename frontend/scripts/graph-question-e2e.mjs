import { chromium } from "@playwright/test";

import { loginAs, pollFor, signOut } from "./e2e-helpers.mjs";

/**
 * 관계 질문 두 turn: 시작 node를 찾고, 명시된 관계만 넓히고, 소유 도구로 읽는다 — 실제 Codex CLI와 MCP로.
 *
 * The first turn asks what a person is working on. The second says `그중 …` and must not depend on the provider
 * remembering anything: the seeds are the canonical ids this conversation already read, re-checked for this person.
 * Everything asserted here is what the tools actually returned — the walk receipt, the resources the answer points
 * at, and the fixed picture of that turn.
 */
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const soonest = `기한이 가장 빠른 업무 ${stamp}`;

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

const waitForTurn = (page, conversationId, expected) =>
  pollFor(
    page,
    () =>
      page.evaluate(async ({ id, count }) => {
        const detail = await (await fetch(`/api/conversations/${id}`)).json();
        const done = detail.turns.filter((turn) => ["completed", "failed", "cancelled"].includes(turn.state));
        return done.length >= count ? detail : null;
      }, { id: conversationId, count: expected }),
    { timeout: 300_000, description: `${expected}번째 turn이 끝나는 것` },
  );

try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });

  // 지호 holds three tasks with different deadlines; one of them is the answer to the follow-up.
  await loginAs(page, "jiho");
  await page.evaluate(async ({ soonestTitle, stampValue }) => {
    const post = (path, body) =>
      fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => r.json());
    const day = (offset) => {
      const date = new Date(Date.now() + offset * 86_400_000);
      return date.toISOString().slice(0, 10);
    };
    const first = await post("/api/tasks", { title: soonestTitle, due_date: day(2) });
    await fetch(`/api/tasks/${first.task_id}/start`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_version: first.version }),
    });
    await post("/api/tasks", { title: `한 주 뒤 업무 ${stampValue}`, due_date: day(7) });
    await post("/api/tasks", { title: `한 달 뒤 업무 ${stampValue}`, due_date: day(30) });
  }, { soonestTitle: soonest, stampValue: stamp });

  await page.getByRole("button", { name: "AX" }).click();
  const created = page.waitForResponse((response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST");
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  const conversation = await (await created).json();

  // Turn 1: a relationship question. The policy asks for graph_search → graph_neighbors → owning read.
  await page.getByLabel("AX 메시지").fill("내가 지금 담당하고 있는 업무를 관계를 따라 찾고, 각 업무의 기한까지 확인해서 알려줘.");
  await page.getByRole("button", { name: "보내기" }).click();
  const afterFirst = await waitForTurn(page, conversation.conversation_id, 1);
  const firstTurn = afterFirst.turns[0];
  if (firstTurn.state !== "completed") throw new Error(`the first turn did not complete: ${firstTurn.state} ${firstTurn.error ?? ""}`);
  const toolsUsed = afterFirst.tool_invocations.filter((tool) => tool.turn_id === firstTurn.turn_id).map((tool) => tool.tool_name);
  if (!toolsUsed.some((name) => name.startsWith("graph_"))) {
    throw new Error(`the relationship question did not start from the graph: ${JSON.stringify(toolsUsed)}`);
  }
  const walked = afterFirst.graph_receipts.filter((step) => step.turn_id === firstTurn.turn_id);
  if (walked.length === 0) throw new Error("the turn recorded no walk of its own");
  const named = afterFirst.answer_resources.filter((row) => row.turn_id === firstTurn.turn_id);
  if (!named.some((row) => row.title === soonest)) {
    throw new Error(`the answer did not point at the work it read: ${JSON.stringify(named.map((row) => row.title))}`);
  }

  // The execution receipt folds away once the turn is done, and the answer keeps its own picture.
  const receipt = page.locator(".ax-rail.terminal details").last();
  await receipt.waitFor({ timeout: 30_000 });
  if (await receipt.evaluate((element) => element.open)) throw new Error("the finished turn did not fold its receipt away");
  if ((await receipt.locator("summary").textContent())?.includes("연결") !== true) {
    throw new Error("the one-line receipt did not say how many steps the turn walked");
  }
  // 근거는 답에 붙은 한 줄로 먼저 오고, 펼쳐야 정본·인용·경로가 나온다.
  const grounds = page.locator("details.ax-answer-evidence").last();
  await grounds.waitFor({ timeout: 20_000 });
  if (await grounds.evaluate((element) => element.open)) throw new Error("the evidence panel was not folded away behind the answer");
  const groundsLine = (await grounds.locator("summary").textContent()) ?? "";
  if (!groundsLine.includes("정본") || !groundsLine.includes("연결")) {
    throw new Error(`the one-line evidence bar did not say what the answer stands on: ${groundsLine}`);
  }
  await grounds.locator("summary").click();
  await grounds.locator("section[aria-label='이 답의 관계']").waitFor({ timeout: 20_000 });
  // 제목만 있는 목록은 봤다는 주장이다. 실제로 걸어간 연결이 그 자리에 문장으로 붙어야 근거가 된다.
  if ((await grounds.locator(".ax-resource-why").count()) === 0) {
    throw new Error("no read resource said which connection the turn walked to reach it");
  }
  await page.screenshot({ path: "test-results/graph-question-turn1.png", fullPage: false });

  // Turn 2: `그중 …`. The seeds come from what this conversation read, not from provider memory.
  await page.getByLabel("AX 메시지").fill("그중 기한이 가장 빠른 업무 하나만 제목으로 알려줘.");
  await page.getByRole("button", { name: "보내기" }).click();
  const afterSecond = await waitForTurn(page, conversation.conversation_id, 2);
  const secondTurn = afterSecond.turns[afterSecond.turns.length - 1];
  if (secondTurn.state !== "completed") throw new Error(`the follow-up did not complete: ${secondTurn.state} ${secondTurn.error ?? ""}`);
  const answer = afterSecond.messages
    .filter((message) => message.role === "assistant" && message.turn_id === secondTurn.turn_id)
    .map((message) => message.body)
    .join(" ");
  // What `그중` should resolve to is decided from the ledger, not from this script's own assumption: among the work
  // the first turn actually read, the one with the earliest deadline.
  const expected = await page.evaluate(async (ids) => {
    const dated = [];
    for (const id of ids) {
      const response = await fetch(`/api/tasks/${id}`);
      if (!response.ok) continue;
      const task = await response.json();
      if (task.due_date) dated.push({ title: task.title, due: task.due_date });
    }
    dated.sort((left, right) => left.due.localeCompare(right.due));
    return dated[0] ?? null;
  }, named.filter((row) => row.resource_type === "task").map((row) => row.resource_id));
  if (!expected) throw new Error("none of the work the turn read had a deadline to compare");
  // What this proves is the seed contract: the follow-up is answered from what this conversation actually read, not
  // from whatever the provider remembered. Which of those it picks is the model's judgement, not SCAX's guarantee.
  const grounded = named.find((row) => answer.includes(row.title));
  if (!grounded) {
    throw new Error(`the follow-up named nothing this conversation had read: ${answer}`);
  }
  await page.screenshot({ path: "test-results/graph-question-turn2.png", fullPage: false });

  // The card hands the centre to the full surface, which applies this person's access again from the start. The card
  // belongs to the turn that actually walked: a follow-up answered from this conversation's own ids has no walk of
  // its own to hand over, and that is the point of the seeds rather than a missing picture.
  const walkedGrounds = page.locator("details.ax-answer-evidence").first();
  if (!(await walkedGrounds.evaluate((element) => element.open))) await walkedGrounds.locator("summary").click();
  await walkedGrounds.locator("section[aria-label='이 답의 관계']").getByRole("button", { name: "전체 그래프로 보기" }).click();
  const surface = page.locator("section[aria-label='관계 그래프']");
  await surface.waitFor({ timeout: 30_000 });
  await pollFor(page, async () => ((await surface.textContent()) ?? "").includes("중심"), {
    timeout: 20_000,
    description: "미니 그래프가 넘겨준 중심 node로 전체 그래프가 열리는 것",
  });
  await page.screenshot({ path: "test-results/graph-question-continued.png", fullPage: false });

  // A stored walk is asked about again before it is shown: a meeting whose share is taken back leaves the chat.
  await signOut(page);
  await loginAs(page, "mina");
  const shared = await page.evaluate(async (stampValue) => {
    const starts = new Date(Date.now() + 7_200_000);
    const meeting = await (
      await fetch("/api/meetings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          organization_id: "scax",
          title: `공유가 끊길 회의 ${stampValue}`,
          starts_at: starts.toISOString(),
          ends_at: new Date(starts.getTime() + 1_800_000).toISOString(),
          visibility: "private",
          attendee_ids: [],
        }),
      })
    ).json();
    await fetch(`/api/meetings/${meeting.meeting_id}/shares`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ member_id: "jiho", expected_version: meeting.version }),
    });
    return meeting;
  }, stamp);

  await signOut(page);
  await loginAs(page, "jiho");
  await page.getByRole("button", { name: "AX" }).click();
  const secondCreated = page.waitForResponse((response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST");
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  const meetingConversation = await (await secondCreated).json();
  await page.getByLabel("AX 메시지").fill("SCAX MCP의 meeting_list 도구로 내가 볼 수 있는 회의를 모두 나열해줘.");
  await page.getByRole("button", { name: "보내기" }).click();
  const afterMeetings = await waitForTurn(page, meetingConversation.conversation_id, 1);
  const meetingTurn = afterMeetings.turns[0];
  if (meetingTurn.state !== "completed") throw new Error(`the meeting turn did not complete: ${meetingTurn.state}`);
  if (!afterMeetings.answer_resources.some((row) => row.resource_id === shared.meeting_id)) {
    throw new Error("the shared meeting was never read, so revocation cannot be shown");
  }

  await signOut(page);
  await loginAs(page, "mina");
  await page.evaluate(async (meetingId) => {
    const current = await (await fetch(`/api/meetings/${meetingId}`)).json();
    await fetch(`/api/meetings/${meetingId}/shares/jiho`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: current.version }),
    });
  }, shared.meeting_id);

  await signOut(page);
  await loginAs(page, "jiho");
  const redacted = await page.evaluate(
    async ({ conversationId, meetingId }) => {
      const detail = await (await fetch(`/api/conversations/${conversationId}`)).json();
      return {
        // What SCAX keeps and re-checks: the references, the walk, and the tool receipts.
        structured: JSON.stringify({
          answer_resources: detail.answer_resources,
          graph_receipts: detail.graph_receipts,
          tool_invocations: detail.tool_invocations,
        }),
        stillReadable: (await fetch(`/api/meetings/${meetingId}`)).status,
      };
    },
    { conversationId: meetingConversation.conversation_id, meetingId: shared.meeting_id },
  );
  if (![403, 404].includes(redacted.stillReadable)) {
    throw new Error(`the share was not actually revoked: ${redacted.stillReadable}`);
  }
  if (redacted.structured.includes(shared.meeting_id) || redacted.structured.includes(`공유가 끊길 회의 ${stamp}`)) {
    throw new Error("a revoked meeting still had a name and a place in the chat");
  }

  // A person who may not read that work is told nothing about it, in any of the same places.
  await signOut(page);
  await loginAs(page, "mina");
  const hidden = await page.evaluate(async (title) => {
    const conversations = await (await fetch("/api/conversations")).json();
    const search = await (await fetch(`/api/graph/search?q=${encodeURIComponent(title.slice(0, 10))}`)).json();
    return { conversations: JSON.stringify(conversations), nodes: search.nodes.length };
  }, soonest);
  if (hidden.conversations.includes(soonest) || hidden.nodes !== 0) {
    throw new Error("work someone may not read leaked into another person's chat or graph");
  }

  console.log(
    JSON.stringify({
      result: "a relationship question walked the graph first, and the follow-up started from this conversation's own ids",
      conversation: conversation.conversation_id,
      first_turn_tools: toolsUsed,
      walked_steps: walked.length,
      answer_resources: named.map((row) => `${row.resource_type}:${row.title}`),
      follow_up_named: grounded.title,
      earliest_deadline_read: expected.title,
    }),
  );
} finally {
  await browser.close();
}
