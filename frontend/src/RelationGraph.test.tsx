import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ graphSearch: vi.fn(), graphNeighbors: vi.fn(), graphOverview: vi.fn() }));

import * as api from "./api";
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

  it("narrows by state and by period, and never hides what the ledger plans no date for", async () => {
    renderPage();
    const graph = await screen.findByLabelText("관계 그래프");
    await waitFor(() => expect(within(graph).getByText(/3개 노드 · 2개 연결/)).toBeTruthy());
    const filters = within(graph).getByLabelText("좁혀 보기");

    // 진행 중인 업무만 빼면 그 업무와 그 연결이 사라진다.
    fireEvent.click(within(filters).getByRole("button", { name: "진행 중" }));
    await waitFor(() => expect(within(graph).getByText(/2개 노드 · 1개 연결/)).toBeTruthy());
    fireEvent.click(within(filters).getByRole("button", { name: "진행 중" }));

    // 날짜가 없는 사람·팀은 기간으로 좁혀도 남는다. 기한이 먼 업무만 빠진다.
    fireEvent.change(within(filters).getByRole("combobox"), { target: { value: "7" } });
    await waitFor(() => expect(within(graph).getByText(/2개 노드 · 1개 연결/)).toBeTruthy());
    expect(api.graphOverview).toHaveBeenCalledTimes(1);
  });
});
