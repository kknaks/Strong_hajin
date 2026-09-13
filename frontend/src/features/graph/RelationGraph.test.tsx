import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", () => ({ graphSearch: vi.fn(), graphNeighbors: vi.fn(), graphOverview: vi.fn() }));

/* jsdom 에는 WebGL 이 없다 — 그래프 화면이 늦게 부르는 `sigma` 가 모듈을 읽는 순간 상수를 찾다 터지고,
   그 거절이 테스트 밖에서 떠 스위트를 빨갛게 만든다. 여기서는 그림을 보지 않으니 빈 껍데기면 된다. */
vi.stubGlobal("WebGL2RenderingContext", class {});
vi.stubGlobal("WebGLRenderingContext", class {});

import * as api from "../../lib/api";
import { RelationGraphPage } from "./RelationGraphPage";

const task = { kind: "task", id: "task-1", title: "분기 마감", state: "in_progress" };
const request = { kind: "work_request", id: "req-1", title: "분기 마감 요청", state: "accepted" };

const overview = {
  center: { kind: "person", id: "mina", title: "민아 (구성원)", state: null },
  nodes: [
    { kind: "person", id: "mina", title: "민아 (구성원)", state: null },
    { kind: "team", id: "product", title: "제품팀", state: null },
    { ...task, date: "2099-01-01" },
  ],
  edges: [
    { kind: "belongs_to", from: "person:mina", to: "team:product", label: "소속", inverse_label: "구성원", provenance: "membership" },
    { kind: "holds", from: "person:mina", to: "task:task-1", label: "담당함", inverse_label: "담당자", provenance: "task_assignment" },
  ],
  truncated: false,
  view: "member",
  available_views: ["member", "team"],
};

function renderPage(onOpenTask = vi.fn()) {
  vi.mocked(api.graphOverview).mockResolvedValue(overview as never);
  render(<RelationGraphPage onError={vi.fn()} onOpenTask={onOpenTask} />);
  return { onOpenTask };
}

describe("관계 탐색", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("finds work by name and shows nothing until someone asks", async () => {
    vi.mocked(api.graphSearch).mockResolvedValue({ query: "분기", nodes: [task, request], truncated: false } as never);
    renderPage();
    expect(api.graphSearch).not.toHaveBeenCalled();
    expect(screen.getByText(/무엇부터 볼지/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("무엇을 찾을까요"), { target: { value: "분기" } });
    fireEvent.click(screen.getByRole("button", { name: "찾기" }));
    await waitFor(() => expect(api.graphSearch).toHaveBeenCalledWith("분기"));
    const results = await screen.findByLabelText("검색 결과");
    expect(within(results).getAllByRole("listitem")).toHaveLength(2);
    expect(within(results).getByText("분기 마감 요청")).toBeTruthy();
  });

  it("walks one hop and says how each thing is connected", async () => {
    vi.mocked(api.graphSearch).mockResolvedValue({ query: "분기", nodes: [task], truncated: false } as never);
    vi.mocked(api.graphNeighbors).mockResolvedValue({
      center: task,
      nodes: [task, request, { kind: "person", id: "jiho", title: "지호 (팀장)", state: null }],
      edges: [
        { kind: "produced", from: "work_request:req-1", to: "task:task-1" },
        { kind: "holds", from: "person:jiho", to: "task:task-1" },
      ],
      truncated: false,
    } as never);
    const { onOpenTask } = renderPage();
    fireEvent.change(screen.getByLabelText("무엇을 찾을까요"), { target: { value: "분기" } });
    fireEvent.click(screen.getByRole("button", { name: "찾기" }));
    fireEvent.click(await screen.findByRole("button", { name: "분기 마감 중심으로 보기" }));

    await waitFor(() => expect(api.graphNeighbors).toHaveBeenCalledWith("task:task-1"));
    const around = await screen.findByLabelText("연결");
    const rows = within(around).getAllByRole("listitem").map((row) => row.textContent);
    // Each line reads as a sentence about the connection, not as an edge type.
    expect(rows.some((row) => row?.includes("이 업무를 만든 요청") && row.includes("분기 마감 요청"))).toBe(true);
    expect(rows.some((row) => row?.includes("담당") && row.includes("지호"))).toBe(true);

    // Opening the original leaves the graph and lands on the work itself.
    fireEvent.click(within(around).getByRole("button", { name: "분기 마감 요청 열기" }));
    expect(api.graphNeighbors).toHaveBeenCalledWith("work_request:req-1");
    fireEvent.click(screen.getByRole("button", { name: "원본 업무 열기" }));
    expect(onOpenTask).toHaveBeenCalledWith("task-1");
  });

  it("says a search was cut short rather than pretending it was everything", async () => {
    vi.mocked(api.graphSearch).mockResolvedValue({ query: "분기", nodes: [task], truncated: true } as never);
    renderPage();
    fireEvent.change(screen.getByLabelText("무엇을 찾을까요"), { target: { value: "분기" } });
    fireEvent.click(screen.getByRole("button", { name: "찾기" }));
    expect(await screen.findByText(/더 있습니다/)).toBeTruthy();
  });
});


describe("첫 화면", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows what this person is already connected to, before anyone searches", async () => {
    renderPage();
    await waitFor(() => expect(api.graphOverview).toHaveBeenCalledWith("member"));
    const graph = await screen.findByLabelText("관계 그래프");
    // The first screen says what it is showing and how much of it, without anyone searching.
    expect(within(graph).getByText(/첫 화면 · 구성원 보기/)).toBeTruthy();
    expect(within(graph).getByText(/3개 노드 · 2개 연결/)).toBeTruthy();
    expect(within(graph).getAllByRole("button", { name: /사람|팀|업무/ }).length).toBeGreaterThan(0);
    expect(api.graphSearch).not.toHaveBeenCalled();
  });

  it("reads the same answer one level up when someone groups by team", async () => {
    renderPage();
    await waitFor(() => expect(api.graphOverview).toHaveBeenCalledWith("member"));
    fireEvent.click(screen.getByRole("tab", { name: "팀으로 묶기" }));
    await waitFor(() => expect(api.graphOverview).toHaveBeenCalledWith("team"));
  });
});


describe("보는 방식", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("hides a kind on request without asking a different question", async () => {
    renderPage();
    const graph = await screen.findByLabelText("관계 그래프");
    await waitFor(() => expect(within(graph).getByText(/3개 노드 · 2개 연결/)).toBeTruthy());

    const filters = within(graph).getByLabelText("좁혀 보기");
    fireEvent.click(within(filters).getByRole("button", { name: /팀/ }));

    // The same authorized answer, drawn with less in it — no new request went out.
    await waitFor(() => expect(within(graph).getByText(/2개 노드 · 1개 연결/)).toBeTruthy());
    expect(api.graphOverview).toHaveBeenCalledTimes(1);
  });

  it("offers nothing but node type: the common surface is not a query builder", async () => {
    /**
     * 설계 정본: 공통 관계 탐색 UI는 기본 이름·제목 검색과 화면에 표시할 node type 선택만 제공한다.
     *
     * 상태·기간 같은 범용 조건은 실제 업무 탐색보다 개발자용 query builder에 가깝다. 질문에 맞는 타입별 조건은
     * Tool이 내부 계약으로 갖고, 각 제품 화면의 고유 목록 filter는 그 화면이 따로 갖는다.
     */
    renderPage();
    const graph = await screen.findByLabelText("관계 그래프");
    const filters = within(graph).getByLabelText("좁혀 보기");

    expect(within(filters).queryByRole("combobox")).toBeNull();
    const offered = within(filters).getAllByRole("button").map((button) => button.textContent?.trim());
    expect(offered).toEqual(["사람", "팀", "업무"]);
    expect(offered).not.toContain("진행 중");

    // 종류를 끄는 것은 여전히 보는 방법이다: 같은 답을 덜 그릴 뿐 서버에 새로 묻지 않는다.
    fireEvent.click(within(filters).getByRole("button", { name: "업무" }));
    await waitFor(() => expect(within(graph).getByText(/2개 노드 · 1개 연결/)).toBeTruthy());
    expect(api.graphOverview).toHaveBeenCalledTimes(1);
  });
});

/* ★ 바퀴 9 §4 — 그래프 색은 **런타임에 CSS 변수를 읽어서** 나온다(sigma 가 WebGL 이라 CSS 변수를
   모른다). 그래서 토큰 이름을 바꿀 때 읽는 쪽(`GraphCanvas.tsx`)을 같이 안 고치면 **테스트는 다
   통과하는데 화면에서 색만 사라진다.** 이 검사가 그 «조용한 실패» 를 막는 자리다 —
   CSS 파일이 정의하는 이름과 코드가 읽는 이름이 **같은 집합**인지 본다. */
it("그래프가 읽는 토큰 이름이 CSS 가 정의하는 이름과 같다 (색이 조용히 사라지는 것을 막는다)", async () => {
  // @ts-expect-error — 이 리포는 @types/node 를 두지 않는다.
  const { readFileSync } = await import("node:fs");
  const css: string = readFileSync("src/styles/scax.css", "utf8");
  const source: string = readFileSync("src/features/graph/GraphCanvas.tsx", "utf8");

  const defined = new Set<string>((css.match(/--scax-graph-[a-z-]+(?=\s*:)/g) ?? []) as string[]);
  const read = new Set<string>((source.match(/--scax-graph-[a-z-]+/g) ?? []) as string[]);

  expect(defined.size).toBe(15);
  expect(read.size).toBeGreaterThan(0);
  // 읽는데 정의가 없는 이름이 하나라도 있으면 그 색은 빈 문자열로 나간다
  for (const name of read) expect(defined.has(name)).toBe(true);
  // 구 이름이 코드에 남아 있으면 안 된다
  expect(source).not.toMatch(/"--graph-/);
});

