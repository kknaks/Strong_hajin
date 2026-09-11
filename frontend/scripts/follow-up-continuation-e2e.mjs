import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");
  const fixtures = await page.evaluate(async () => {
    const request = (url, body) => fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(async (response) => {
      if (!response.ok) throw new Error(`${url} fixture failed: ${response.status} ${await response.text()}`);
      return response.json();
    });
    const [task, meeting] = await Promise.all([
      request("/api/tasks", { title: "Q9 출시 점검", description: "후속 업무 후보 검증", due_date: "2026-09-18" }),
      request("/api/meetings", {
        organization_id: "scax",
        title: "Q9 출시 회의",
        starts_at: "2026-09-17T01:00:00Z",
        ends_at: "2026-09-17T02:00:00Z",
        visibility: "private",
        attendee_ids: ["mina"],
      }),
    ]);
    return { task_id: task.task_id, meeting_id: meeting.meeting_id };
  });
  const domainCounts = await page.evaluate(async () => {
    const [tasks, meetings] = await Promise.all([
      fetch("/api/tasks").then((response) => response.json()),
      fetch("/api/meetings").then((response) => response.json()),
    ]);
    return { tasks: tasks.length, meetings: meetings.length };
  });

  const conversationsLoaded = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "GET",
  );
  await page.getByRole("button", { name: "AX", exact: true }).click();
  await conversationsLoaded;
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  await page.getByLabel("AX 메시지").fill(
    [
      "SCAX MCP의 list_meetings와 task_list를 사용해 내 회의와 업무 현황을 짧게 요약해줘.",
      "완료 뒤에는 지금 답변에서 자연스럽게 이어지는 회의 또는 업무 후속 질문만 제안해줘.",
      "Task나 Meeting은 만들지 말고 조회 결과만 답해줘.",
    ].join(" "),
  );
  const created = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "보내기" }).click();
  const conversation = await (await created).json();

  const projected = await pollFor(
    page,
    () => page.evaluate(async (conversationId) => {
      const response = await fetch(`/api/conversations/${conversationId}`);
      if (!response.ok) return null;
      const current = await response.json();
      const completed = current.turns.find((turn) => turn.state === "completed" && turn.follow_up_candidates?.length >= 2);
      return completed ? { current, completed } : null;
    }, conversation.conversation_id),
    { timeout: 120_000, description: "a completed answer with 2–3 follow-up candidates" },
  );
  const candidates = projected.completed.follow_up_candidates;
  if (candidates.length < 2 || candidates.length > 3) {
    throw new Error(`follow-up count is outside 2–3: ${JSON.stringify(candidates)}`);
  }
  if (new Set(candidates.map((candidate) => candidate.user_text.replace(/\s+/g, " ").trim().toLocaleLowerCase("ko"))).size !== candidates.length) {
    throw new Error(`follow-ups are duplicated: ${JSON.stringify(candidates)}`);
  }
  if (candidates.some((candidate) => candidate.source_turn_id !== projected.completed.turn_id)) {
    throw new Error(`follow-up source turn is not stable: ${JSON.stringify(candidates)}`);
  }

  const group = page.getByRole("region", { name: "추천 대화" });
  await group.waitFor({ timeout: 20_000 });
  const buttons = group.locator("button.ax-follow-up-candidate");
  if ((await buttons.count()) !== candidates.length) throw new Error("browser did not render every projected candidate");
  const answerLayout = await page.locator(".assistant.final").first().evaluate((element) => {
    const character = element.querySelector(".assistant-character");
    const body = element.querySelector(".ax-assistant-body");
    const speaker = element.querySelector(".ax-assistant-speaker");
    const paragraph = element.querySelector(".ax-md p");
    return {
      hasCharacter: Boolean(character),
      bodyWidth: body?.getBoundingClientRect().width ?? 0,
      background: getComputedStyle(element).backgroundColor,
      borderStyle: getComputedStyle(element).borderStyle,
      hasSpeaker: Boolean(speaker),
      bodyFontSize: paragraph ? getComputedStyle(paragraph).fontSize : null,
    };
  });
  if (
    answerLayout.hasCharacter
    || answerLayout.bodyWidth < 300
    || answerLayout.background !== "rgba(0, 0, 0, 0)"
    || answerLayout.borderStyle !== "none"
    || answerLayout.hasSpeaker
    || answerLayout.bodyFontSize !== "14px"
  ) {
    throw new Error(`answer repeated its character or collapsed: ${JSON.stringify(answerLayout)}`);
  }
  const viewportEvidence = {};
  for (const width of [1440, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    const layout = await group.evaluate((element) => {
      const list = element.querySelector(".ax-follow-up-list");
      const cards = [...element.querySelectorAll("button.ax-follow-up-candidate")];
      const label = element.querySelector("button span");
      const labelStyle = label ? getComputedStyle(label) : null;
      const turn = element.closest(".ax-turn");
      const request = turn?.querySelector("p.user");
      const requestStyle = request ? getComputedStyle(request) : null;
      const listWidth = list?.getBoundingClientRect().width ?? 0;
      return {
        height: element.getBoundingClientRect().height,
        pageClientWidth: document.documentElement.clientWidth,
        pageScrollWidth: document.documentElement.scrollWidth,
        listWidth,
        cardWidths: cards.map((card) => card.getBoundingClientRect().width),
        cardFontSize: labelStyle?.fontSize ?? null,
        whiteSpace: labelStyle?.whiteSpace ?? null,
        requestFontSize: requestStyle?.fontSize ?? null,
        requestPadding: requestStyle?.padding ?? null,
        requestRightGap: turn && request ? turn.getBoundingClientRect().right - request.getBoundingClientRect().right : null,
        lastInTurn: element === turn?.lastElementChild,
      };
    });
    if (layout.height < 120 || layout.height > 230 || layout.pageScrollWidth > layout.pageClientWidth || !layout.lastInTurn) {
      throw new Error(`stacked follow-up cards broke at ${width}px: ${JSON.stringify(layout)}`);
    }
    if (layout.cardWidths.some((cardWidth) => Math.abs(cardWidth - layout.listWidth) > 1)) {
      throw new Error(`follow-up cards are not full width at ${width}px: ${JSON.stringify(layout)}`);
    }
    if (layout.cardFontSize !== "12px" || layout.whiteSpace !== "normal") {
      throw new Error(`follow-up typography is not compact at ${width}px: ${JSON.stringify(layout)}`);
    }
    if (layout.requestFontSize !== "14px" || layout.requestPadding !== "12px 14px" || layout.requestRightGap !== 0) {
      throw new Error(`request bubble scale or alignment broke at ${width}px: ${JSON.stringify(layout)}`);
    }
    const accessibleNames = await buttons.evaluateAll((items) => items.map((item) => item.getAttribute("aria-label")));
    if (accessibleNames.some((name, index) => name !== candidates[index].user_text)) {
      throw new Error(`follow-up accessible name lost user text: ${JSON.stringify(accessibleNames)}`);
    }
    viewportEvidence[width] = layout;
    await page.screenshot({ path: `test-results/follow-up-continuation-${width}.png`, fullPage: true });
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await buttons.first().focus();
  await page.keyboard.press("ArrowDown");
  if (await page.locator(":focus").getAttribute("aria-label") !== candidates[1].user_text) {
    throw new Error("ArrowDown did not move focus along the follow-up cards");
  }
  await page.keyboard.press("ArrowUp");
  if (await page.locator(":focus").getAttribute("aria-label") !== candidates[0].user_text) {
    throw new Error("ArrowUp did not move focus back along the follow-up cards");
  }
  await page.getByLabel("AX 메시지").fill("보존해야 하는 수동 작성 초안");

  const selected = candidates[0];
  let droppedReceipt = null;
  let droppedKey = null;
  let droppedPayload = null;
  let dropFirstResponse = true;
  await page.route(`**/api/conversations/${conversation.conversation_id}/messages`, async (route) => {
    const request = route.request();
    const body = request.postDataJSON();
    if (body.follow_up_candidate_id === selected.candidate_id && dropFirstResponse) {
      dropFirstResponse = false;
      droppedKey = request.headers()["idempotency-key"];
      droppedPayload = body;
      const upstream = await route.fetch();
      droppedReceipt = await upstream.json();
      await route.abort("failed");
      return;
    }
    await route.continue();
  });

  const selectedButton = group.getByRole("button", { name: selected.user_text, exact: true });
  await selectedButton.focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter");
  await pollFor(page, async () => droppedReceipt, { timeout: 20_000, description: "the accepted follow-up after its response is lost" });
  await group.waitFor({ state: "detached", timeout: 20_000 });

  const retriedResponse = page.waitForResponse(
    (response) => response.url().endsWith(`/api/conversations/${conversation.conversation_id}/messages`)
      && response.request().postDataJSON()?.follow_up_candidate_id === selected.candidate_id,
  );
  await page.evaluate(async ({ conversationId, idempotencyKey, payload }) => {
    await fetch(`/api/conversations/${conversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(payload),
    });
  }, { conversationId: conversation.conversation_id, idempotencyKey: droppedKey, payload: droppedPayload });
  const retried = await retriedResponse;
  if (retried.status() !== 202) throw new Error(`follow-up retry returned ${retried.status()}`);
  const retriedReceipt = await retried.json();
  const retriedKey = retried.request().headers()["idempotency-key"];
  if (retriedReceipt.message_id !== droppedReceipt.message_id || retriedReceipt.turn_id !== droppedReceipt.turn_id) {
    throw new Error(`lost-response retry created another Turn: ${JSON.stringify({ droppedReceipt, retriedReceipt })}`);
  }
  if (droppedKey !== `follow-up:${selected.candidate_id}` || retriedKey !== droppedKey) {
    throw new Error(`follow-up idempotency identity changed: ${JSON.stringify({ droppedKey, retriedKey })}`);
  }

  if (await page.getByLabel("AX 메시지").inputValue() !== "보존해야 하는 수동 작성 초안") {
    throw new Error("follow-up selection replaced the manual composer draft");
  }
  const evidence = await page.evaluate(async ({ conversationId, candidateId }) => {
    const [current, tasks, meetings] = await Promise.all([
      fetch(`/api/conversations/${conversationId}`).then((response) => response.json()),
      fetch("/api/tasks").then((response) => response.json()),
      fetch("/api/meetings").then((response) => response.json()),
    ]);
    return {
      selectedMessages: current.messages.filter((message) => message.follow_up_candidate_id === candidateId),
      tasks: tasks.length,
      meetings: meetings.length,
    };
  }, { conversationId: conversation.conversation_id, candidateId: selected.candidate_id });
  if (evidence.selectedMessages.length !== 1 || evidence.selectedMessages[0].body !== selected.user_text) {
    throw new Error(`follow-up did not become exactly one ordinary user message: ${JSON.stringify(evidence.selectedMessages)}`);
  }
  if (evidence.tasks !== domainCounts.tasks || evidence.meetings !== domainCounts.meetings) {
    throw new Error(`candidate selection mutated Task/Meeting without approval: ${JSON.stringify({ before: domainCounts, after: evidence })}`);
  }

  await page.screenshot({ path: "test-results/follow-up-continuation-720-selected.png", fullPage: true });
  console.log(JSON.stringify({
    result: "follow-up continued one Conversation exactly once after a lost response",
    conversation_id: conversation.conversation_id,
    source_turn_id: selected.source_turn_id,
    candidate_count: candidates.length,
    selected_candidate_id: selected.candidate_id,
    message_id: retriedReceipt.message_id,
    fixtures,
    domain_counts: domainCounts,
    viewport_evidence: viewportEvidence,
    answer_layout: answerLayout,
  }));
} finally {
  await browser.close();
}
