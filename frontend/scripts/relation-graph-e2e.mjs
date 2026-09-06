import { chromium } from "@playwright/test";

import { loginAs, pollFor, signOut } from "./e2e-helpers.mjs";

// 관계 탐색: a manager searches for work and follows how it came about — request → work → its parts and materials —
// then opens the work itself. Every hop is the server's authorized answer, and AX walks the same graph through MCP.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const title = `관계 탐색 업무 ${stamp}`;

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

  // A request that mina sent and jiho accepted, with a part and an output under it.
  await page.evaluate(async (subject) => {
    await fetch("/api/work-requests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: subject, assignee_id: "jiho" }),
    });
  }, title);

  await signOut(page);
  await loginAs(page, "jiho");
  const judgement = await pollFor(
    page,
    () =>
      page.evaluate(async (subject) => {
        const items = await (await fetch("/api/action-items")).json();
        return items.find((item) => item.subject === subject) ?? null;
      }, title),
    { timeout: 20_000, description: "the request to reach the assignee" },
  );
  const built = await page.evaluate(async ({ actionItemId, expectedVersion, subject }) => {
    await fetch(`/api/action-items/${actionItemId}/commands/accept`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: expectedVersion }),
    });
    const work = await (await fetch("/api/my-work")).json();
    const task = work.find((row) => row.title === subject);
    const child = await (await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: `${subject} 하위`, parent_task_id: task.task_id }),
    })).json();
    await fetch(`/api/tasks/${task.task_id}/materials/links`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "output", url: "https://docs.example.com/graph", label: `결과 문서 ${subject}` }),
    });
    return { taskId: task.task_id, childId: child.task_id };
  }, { actionItemId: judgement.action_item_id, expectedVersion: judgement.expected_version, subject: title });

  // Enough connected work that the first screen is a real graph rather than a handful of dots: the label placement,
  // the hover highlight and the detail panel all have to hold at this size.
  await page.evaluate(async (stampValue) => {
    const post = (path, body) =>
      fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((response) => response.json());
    for (let index = 0; index < 6; index += 1) {
      await post("/api/tasks", { title: `제품팀 진행 업무 ${index + 1} ${stampValue}` });
    }
    const starts = new Date(Date.now() + 3_600_000);
    for (const [index, attendees] of [["mina"], ["mina", "yuna"], ["yuna"]].entries()) {
      await post("/api/meetings", {
        organization_id: "scax",
        title: `연결이 보일 회의 ${index + 1} ${stampValue}`,
        starts_at: new Date(starts.getTime() + index * 3_600_000).toISOString(),
        ends_at: new Date(starts.getTime() + (index + 1) * 3_600_000).toISOString(),
        visibility: "private",
        attendee_ids: attendees,
      });
    }
  }, stamp);

  // Work that came from other people, so the first screen has more than one hub: 민아 sends three requests and 지호
  // accepts them, exactly the way the product does it.
  await signOut(page);
  await loginAs(page, "mina");
  await page.evaluate(async (stampValue) => {
    for (let index = 0; index < 3; index += 1) {
      await fetch("/api/work-requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: `민아가 보낸 요청 ${index + 1} ${stampValue}`, assignee_id: "jiho" }),
      });
    }
  }, stamp);
  await signOut(page);
  await loginAs(page, "jiho");
  await page.evaluate(async (stampValue) => {
    const items = await (await fetch("/api/action-items")).json();
    for (const item of items.filter((row) => String(row.subject).includes(`민아가 보낸 요청`) && String(row.subject).includes(String(stampValue)))) {
      await fetch(`/api/action-items/${item.action_item_id}/commands/accept`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_version: item.expected_version }),
      });
    }
  }, stamp);

  // The first screen is already a graph of what this person is connected to — not an empty search box.
  await page.reload({ waitUntil: "domcontentloaded" });
  await navigation.getByRole("button", { name: "관계 탐색" }).click();
  const canvas = page.locator("section[aria-label='관계 그래프']");
  await canvas.waitFor({ timeout: 20_000 });
  await pollFor(page, async () => ((await canvas.textContent()) ?? "").includes("첫 화면 · 구성원 보기"), {
    timeout: 20_000,
    description: "첫 진입 그래프",
  });
  // The picture is actually drawn, not just described: WebGL rendered it with the nodes the server sent.
  const drawnNodes = await pollFor(
    page,
    async () => {
      const element = canvas.locator(".graph-canvas[data-drawn='true']");
      return (await element.count()) > 0 ? Number(await element.getAttribute("data-node-count")) : null;
    },
    { timeout: 20_000, description: "그래프가 실제로 그려지는 것" },
  );
  if (!drawnNodes || drawnNodes < 23) throw new Error(`the graph drew too little to be a graph: ${drawnNodes}`);
  await page.screenshot({ path: "test-results/relation-graph-overview.png", fullPage: false });

  // Hovering a node lights its direct connections and dims the rest — the picture actually changes, it is not a class
  // toggled on an element nobody can see.
  const surface = canvas.locator(".graph-canvas");
  const canvasBox = await surface.boundingBox();
  const before = await surface.screenshot();
  // Walk the surface until the pointer is actually over a node — the cursor is the renderer saying so.
  let hoveredAt = null;
  for (let column = 1; column < 12 && !hoveredAt; column += 1) {
    for (let row = 1; row < 9 && !hoveredAt; row += 1) {
      const point = {
        x: canvasBox.x + (canvasBox.width * column) / 12,
        y: canvasBox.y + (canvasBox.height * row) / 9,
      };
      await page.mouse.move(point.x, point.y);
      await page.waitForTimeout(60);
      if ((await surface.evaluate((element) => element.style.cursor)) === "pointer") hoveredAt = point;
    }
  }
  if (!hoveredAt) throw new Error("the pointer never found a node on the canvas");
  const hoverChanged = await pollFor(
    page,
    async () => {
      const now = await surface.screenshot();
      return Buffer.compare(before, now) !== 0 ? now.length : null;
    },
    { timeout: 10_000, description: "hover가 이웃을 밝히고 나머지를 흐리게 하는 것" },
  );
  await page.screenshot({ path: "test-results/relation-graph-hover.png", fullPage: false });
  await page.mouse.move(canvasBox.x + 4, canvasBox.y + 4);
  const overview = await page.evaluate(async () => {
    const [member, team] = await Promise.all([
      (await fetch("/api/graph/overview?view=member")).json(),
      (await fetch("/api/graph/overview?view=team")).json(),
    ]);
    return {
      kinds: [...new Set(member.nodes.map((node) => node.kind))].sort(),
      provenance: member.edges.every((edge) => Boolean(edge.provenance)),
      teamHasPeople: team.nodes.some((node) => node.kind === "person"),
      teamNodes: team.nodes.filter((node) => node.kind === "team").map((node) => node.id),
      internalEdges: team.edges.filter((edge) => edge.from === edge.to).length,
    };
  });
  if (!overview.kinds.includes("team") || !overview.kinds.includes("task")) {
    throw new Error(`the first screen is missing node kinds: ${JSON.stringify(overview)}`);
  }
  if (!overview.provenance) throw new Error("an edge arrived without saying which ledger states it");
  if (overview.teamHasPeople || overview.internalEdges > 0 || overview.teamNodes.length === 0) {
    throw new Error(`grouping by team did not read one level up: ${JSON.stringify(overview)}`);
  }
  // 표현 수준 전환은 같은 인가된 답을 다시 그린다.
  const views = page.getByRole("tablist", { name: "표현 수준" });
  await views.getByRole("tab", { name: "팀으로 묶기" }).click();
  await pollFor(page, async () => (await views.getByRole("tab", { name: "팀으로 묶기" }).getAttribute("aria-selected")) === "true", {
    timeout: 15_000,
    description: "팀으로 묶기 전환",
  });
  await views.getByRole("tab", { name: "구성원 보기" }).click();

  // The manager follows it from the search box.
  await page.getByLabel("무엇을 찾을까요").fill(title);
  await page.getByRole("button", { name: "찾기" }).click();
  const results = page.locator("section[aria-label='검색 결과']");
  await results.locator(`li[data-node^="task:"]`).first().getByRole("button").click();

  // Choosing a node from the search fills the right panel with its canonical source and its connections' provenance.
  const detail = page.getByLabel("선택한 노드");
  await pollFor(page, async () => ((await detail.textContent()) ?? "").includes("Task + TaskAssignment"), {
    timeout: 20_000,
    description: "선택한 노드의 정본과 연결",
  });
  const detailText = ((await detail.textContent()) ?? "").replace(/\s+/g, " ");
  if (!detailText.includes("Task + TaskAssignment")) throw new Error(`the detail panel did not name the ledger: ${detailText}`);
  if (!/출처 \S+/.test(detailText)) throw new Error(`a connection arrived without its provenance: ${detailText}`);
  if ((await detail.locator("[data-relation]").count()) === 0) throw new Error("the selected node listed no connections");
  await page.screenshot({ path: "test-results/relation-graph-detail.png", fullPage: false });

  const around = page.locator("section[aria-label='연결']");
  await around.locator("li").first().waitFor({ timeout: 20_000 });
  const connections = ((await around.textContent()) ?? "").replace(/\s+/g, " ");
  for (const expected of ["이 업무를 만든 요청", "담당", "하위 업무", "참고 자료·산출물"]) {
    if (!connections.includes(expected)) throw new Error(`a connection is missing (${expected}): ${connections}`);
  }
  if (!connections.includes("지호")) throw new Error(`the holder is not named: ${connections}`);

  // One more hop, to the request that made it, and then back to the work itself.
  await around.locator('li[data-edge="produced"]').first().getByRole("button").click();
  await page.waitForFunction(
    () => document.querySelector("section[aria-label='연결'] h4")?.textContent?.includes("업무 요청"),
    undefined,
    { timeout: 20_000 },
  );
  await around.locator('li[data-node^="task:"]').first().getByRole("button").click();
  await around.getByRole("button", { name: "원본 업무 열기" }).click();
  const drawer = page.getByRole("dialog", { name: "업무 상세" });
  await drawer.waitFor({ timeout: 20_000 });
  if (!((await drawer.textContent()) ?? "").includes(title)) throw new Error("the graph did not open the work it named");
  await drawer.getByRole("button", { name: "상세 닫기" }).click();

  // Someone who may not read this work finds none of it, and cannot walk into it.
  await page.getByRole("dialog").waitFor({ state: "detached", timeout: 20_000 });
  await signOut(page);
  await loginAs(page, "mina");
  const hidden = await page.evaluate(async ({ childId, subject }) => {
    const found = await (await fetch(`/api/graph/search?q=${encodeURIComponent(subject + " 하위")}`)).json();
    const walked = await fetch(`/api/graph/neighbors?node=task:${childId}`);
    return { nodes: found.nodes.length, status: walked.status, body: await walked.text() };
  }, { childId: built.childId, subject: title });
  if (hidden.nodes !== 0 || ![403, 404].includes(hidden.status) || hidden.body.includes("하위")) {
    throw new Error(`work someone may not read leaked into the graph: ${JSON.stringify(hidden)}`);
  }

  await page.screenshot({ path: "test-results/relation-graph-e2e.png", fullPage: true });
  console.log(
    JSON.stringify({
      result: "the first screen was already a graph, grouped by team, then followed request → work → parts and materials",
      task_id: built.taskId,
      node_kinds: overview.kinds,
      team_nodes: overview.teamNodes,
      drawn_nodes: drawnNodes,
    }),
  );
} finally {
  await browser.close();
}
