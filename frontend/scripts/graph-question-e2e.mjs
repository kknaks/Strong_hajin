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
  await page.getByLabel("AX 메시지").fill("내가 지금 담당하고 있는 업무가 무엇인지 관계를 따라 확인하고 알려줘.");
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
  await page.locator("section[aria-label='이 답의 관계']").last().waitFor({ timeout: 20_000 });
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
  if (!answer.includes(soonest)) throw new Error(`the follow-up did not name the soonest work: ${answer}`);
  await page.screenshot({ path: "test-results/graph-question-turn2.png", fullPage: false });

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
    }),
  );
} finally {
  await browser.close();
}
