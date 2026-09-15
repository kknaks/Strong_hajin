import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async (actual) => ({
  // ApiError 는 진짜를 쓴다 — 화면이 409 를 `instanceof` 로 가른다
  ApiError: ((await actual()) as { ApiError: unknown }).ApiError,
  readMeetingTranscript: vi.fn(),
  readMeetingRooms: vi.fn(),
  readMeetingMaterials: vi.fn(),
  attachMeetingMaterials: vi.fn(),
  detachMeetingMaterial: vi.fn(),
  meetingMaterialContentUrl: (m: string, id: string) => `/api/meetings/${m}/materials/${id}/content`,
  readMeetingShares: vi.fn(),
  shareMeetingWith: vi.fn(),
  revokeMeetingShare: vi.fn(),
  promoteMeetingTodo: vi.fn(),
  removeMeetingTodo: vi.fn(),
  retryMeetingFinalize: vi.fn(),
  meetingExportUrl: (id: string) => `/api/meetings/${id}/export?format=html`,
  readMeeting: vi.fn(),
  startMeeting: vi.fn(),
  endMeeting: vi.fn(),
  updateMeetingInfo: vi.fn(),
  updateMeetingAgenda: vi.fn(),
  addMeetingAgenda: vi.fn(),
  removeMeetingAgenda: vi.fn(),
  addMeetingMemoLine: vi.fn(),
  bookMeeting: vi.fn(),
  getOrganizationTree: vi.fn(),
  getOrganizationUnitMembers: vi.fn(),
  getWorkRequestAssigneeCandidates: vi.fn(),
  getMeetingPromotionCandidates: vi.fn(),
  updateMeetingMemoLine: vi.fn(),
  removeMeetingMemoLine: vi.fn(),
  getWorkRequestCcCandidates: vi.fn(),
  createWorkRequest: vi.fn(),
  createDirectTask: vi.fn(),
  assignTask: vi.fn(),
  getTasks: vi.fn(),
}));

import * as api from "../../lib/api";
import { meetingScreen } from "../../lib/labels";
import type { MeetingAgenda, MeetingInfo, MeetingRecord, MeetingTodo } from "../../lib/viewModels";
import { useState } from "react";

import { MeetingDetailPage } from "./MeetingDetailPage";
import { resetRoster } from "./roster";

/* ── 스트림과 마이크는 모킹한다. 서버는 WP-002 가 만드는 중이고, 브라우저 마이크는 jsdom 에 없다 ── */

type Frame = Record<string, unknown>;

class FakeSocket {
  static instances: FakeSocket[] = [];
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  url: string;
  readyState = 0;
  binaryType = "";
  sent: Array<string | ArrayBuffer> = [];
  closedByClient = false;
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }
  send(data: string | ArrayBuffer) {
    this.sent.push(data);
  }
  close() {
    this.closedByClient = true;
    this.readyState = FakeSocket.CLOSED;
  }
  /** 서버가 핸드셰이크를 받아 준 순간 */
  accept() {
    this.readyState = FakeSocket.OPEN;
    act(() => this.onopen?.({}));
  }
  emit(frame: Frame) {
    act(() => this.onmessage?.({ data: JSON.stringify(frame) }));
  }
  serverClose(code: number, reason = "") {
    this.readyState = FakeSocket.CLOSED;
    act(() => this.onclose?.({ code, reason }));
  }
  /** 첫 프레임(auth)을 그대로 읽는다 */
  get auth(): Frame {
    return JSON.parse(String(this.sent[0])) as Frame;
  }
  get audioChunks(): ArrayBuffer[] {
    return this.sent.slice(1).filter((one): one is ArrayBuffer => typeof one !== "string");
  }
}

class FakeRecorder {
  static instances: FakeRecorder[] = [];
  static lastMime = "";
  state: "inactive" | "recording" = "inactive";
  ondataavailable: ((event: { data: { size: number; arrayBuffer: () => Promise<ArrayBuffer> } }) => void) | null = null;
  constructor(_stream: unknown, options: { mimeType: string }) {
    FakeRecorder.lastMime = options.mimeType;
    FakeRecorder.instances.push(this);
  }
  start() {
    this.state = "recording";
  }
  stop() {
    this.state = "inactive";
  }
}

const socket = () => FakeSocket.instances[FakeSocket.instances.length - 1];

/**
 * 스트림은 회의를 받은 «뒤» 에 붙는다 — 제목이 떴다고 소켓이 이미 선 것은 아니다.
 * 기다리지 않으면 `socket()` 이 `undefined` 로 잡혀 간헐로 터진다 (검수 6 W-2).
 */
async function openedSocket(): Promise<FakeSocket> {
  await waitFor(() => expect(FakeSocket.instances.length).toBeGreaterThan(0));
  return socket();
}

/*
 * v0.5.1: **진행 중 회의에는 회의록이 두 벌 선다** (§4.0 · §4.2-6) — 사람 벌과 AI 벌이 각각
 * 자기 안건 목록을 갖는다. 0.4.x 픽스처는 안건 하나에 AI 줄을 매달아 두 탭이 그것을 나눠 봤는데,
 * 그 모양은 이제 계약이 아니다: 줄은 «자기 벌의 안건에만» 매달린다 (§4.2-9).
 * 그래서 픽스처도 두 벌로 갈랐다 — 이것이 「탭마다 목록이 다르다」를 검사가 실제로 밟게 한다.
 */
const agenda: MeetingAgenda = {
  agenda_id: "a1",
  last_saved_at: "2026-09-08T07:00:00Z",
  order: 1,
  title: "토큰 수요 전망",
  track: "memo" as const,
  title_placeholder: false,
  merged_from: [],
  source: "manual",
  concluded: false,
  lines: [],
  todos: [],
};

/** AI 벌의 안건 하나 — 출처는 `null` 이고(§4.1-2) 줄은 AI 줄이다. */
const aiAgenda: MeetingAgenda = {
  agenda_id: "ai-1",
  last_saved_at: "2026-09-08T07:00:00Z",
  order: 1,
  title: "토큰 수요 전망",
  track: "ai" as const,
  title_placeholder: false,
  merged_from: [],
  source: null,
  concluded: false,
  lines: [{ line_id: "l1", track: "ai", order: 1, text: "회의 전에 AI 가 적어 둔 줄.", author: "AI", at_ms: null, evidence: [], from_lines: [] }],
  todos: [],
};

/** 두 벌이 함께 선 기본 상태 — 진행 중 화면이 실제로 받는 모양이다. */
const bothTracks: MeetingAgenda[] = [agenda, aiAgenda];

/** 후속 업무 후보 하나 — 회의 중 배치가 내는 잠정 후보(provisional)와 종료 뒤 최종이 같은 모양이다. *//* 바퀴 6a: 첨부·스크립트 칸은 셸의 «4칸» 에 포털로 앉는다(M-5). 화면만 떼어 렌더하면 그 자리가
   없으므로, 셸이 내주는 칸만 흉내 내는 얇은 집을 둔다. */
function DetailHost(props: Parameters<typeof MeetingDetailPage>[0]) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  return (
    <>
      <MeetingDetailPage {...props} sideRailHost={host} />
      <div ref={setHost} />
    </>
  );
}


function todo(over: Partial<MeetingTodo> & { todo_id: string; title: string }): MeetingTodo {
  return {
    agenda_id: "a1",
    description: "",
    due_candidate: null,
    checklist_candidate: [],
    reference: { meeting_id: "m1", agenda_id: "a1", line_ids: [] },
    linked: null,
    provisional: true,
    ...over,
  };
}

function meeting(over: Partial<MeetingInfo> = {}): MeetingInfo {
  return {
    meeting_id: "m1",
    title: "DB ax 전략",
    purpose: null,
    starts_at: "2026-09-08T06:30:00Z",
    ends_at: "2026-09-08T07:00:00Z",
    location: "대회의실",
    status: "in_progress",
    created_by: "이건학",
    attendees: [
      { member_id: "1", display_name: "이건학" },
      { member_id: "2", display_name: "정우성" },
    ],
    external_attendees: [],
    viewer_relation: "attendee",
    can_edit_info: false,
    can_edit_note: false,
    can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: false, ai: false, final: false },
    can_write_memo: true,
    started_at: "2026-09-08T06:30:00Z",
    title_candidate: null,
    failure_reason: null,
    room_reservation: null,
    last_saved_at: null,
    carried_from_meeting_id: null,
    ...over,
  };
}

/** 오디오를 올리는 자리와 메모를 쓰는 자리는 **서버가 말한다** — `can_write_memo` 하나로 갈린다. */
function renderLive(canWriteMemo: boolean, over: Partial<MeetingInfo> = {}, agendas: MeetingAgenda[] = [agenda]) {
  const value: MeetingRecord = { meeting: meeting({ can_write_memo: canWriteMemo, ...over }), agendas };
  vi.mocked(api.readMeeting).mockResolvedValue(value);
  const onSessionLost = vi.fn();
  render(
    <DetailHost
      canCreateWorkRequests={false}
      meetingId="m1"
      onBack={vi.fn()}
      onError={vi.fn()}
      onNotice={vi.fn()}
      onOpenMeeting={vi.fn()}
      onSessionLost={onSessionLost}
      ownerName="이건학"
    />,
  );
  return { onSessionLost };
}

/** 스트림 없이 보는 창(참여자·주최자의 두 번째 창)을 세운다 — 소켓을 열지 않는다. */
async function watch(over: Partial<MeetingInfo> = {}, agendas: MeetingAgenda[] = bothTracks) {
  const rendered = renderLive(false, over, agendas);
  await screen.findByText("DB ax 전략");
  return rendered;
}

/** `ready` 까지 밟아 연결을 세운다. */
async function connect(canWriteMemo = true, over: Partial<MeetingInfo> = {}, agendas: MeetingAgenda[] = bothTracks) {
  const rendered = renderLive(canWriteMemo, over, agendas);
  await screen.findByText("DB ax 전략");
  const opened = await openedSocket();
  opened.accept();
  opened.emit({ type: "ready", meetingStartedAt: "2026-09-08T06:30:00Z", latestBatchSeq: 0, speakerCount: 2 });
  return rendered;
}

beforeEach(() => {
  resetRoster();
  FakeSocket.instances = [];
  FakeRecorder.instances = [];
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  Object.defineProperty(window.navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [] }) },
  });
  vi.mocked(api.readMeetingRooms).mockResolvedValue([]);
  vi.mocked(api.getOrganizationTree).mockResolvedValue([]);
  vi.mocked(api.getOrganizationUnitMembers).mockResolvedValue([]);
  vi.mocked(api.readMeetingMaterials).mockResolvedValue([]);
  vi.mocked(api.readMeetingShares).mockResolvedValue([]);
  vi.mocked(api.readMeetingTranscript).mockResolvedValue({ items: [], memos: [] });
  vi.mocked(api.getTasks).mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("SCR-106 「진행 중」 — 회의 스트림", () => {
  it("첫 프레임이 역할을 선언한다 — 오디오를 올리는 사람만 audio 를 싣는다", async () => {
    await connect(true);
    // 같은 오리진이라 쿠키가 핸드셰이크에 실린다 — 주소에 토큰을 붙이지 않는다
    expect(socket().url).toMatch(/^wss?:\/\/[^/]+\/api\/meetings\/m1\/stream$/);
    expect(socket().auth).toEqual({
      type: "auth",
      role: "upstream",
      audio: { format: "webm/opus", sampleRate: 16000, channels: 1 },
    });
  });

  it("참여자는 구독으로 붙어 갱신되는 것을 다 받는다 — 보내는 것은 없다", async () => {
    await connect(false);
    // 같은 회의 스트림 하나에 읽기 전용으로 붙는다
    expect(FakeSocket.instances).toHaveLength(1);
    expect(socket().auth).toEqual({ type: "auth", role: "subscribe" });
    expect(window.navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
    expect(socket().audioChunks).toHaveLength(0);

    // 두 탭은 그대로 서고 조작만 없다
    expect(screen.getByRole("tab", { name: "메모" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "AI 요약" })).toBeTruthy();
    expect(screen.queryByLabelText("메모를 남기세요")).toBeNull();
    expect(screen.queryByRole("button", { name: "회의 종료" })).toBeNull();

    // 확정 스크립트
    socket().emit({ type: "transcript.final", item: { id: "f1", speakerLabel: "1", atMs: 61_000, endMs: 64_000, content: "받아 적힌 말." } });
    fireEvent.click(screen.getByRole("tab", { name: "스크립트" }));
    expect(screen.getByText("받아 적힌 말.")).toBeTruthy();

    // 누가 남긴 메모 — 보는 창도 같은 프레임으로 받는다
    socket().emit({
      type: "memo.line",
      agendaId: "a1",
      line: { line_id: "m1", track: "memo", order: 1, text: "다른 사람이 남긴 메모.", author: "1", at_ms: 70_000, evidence: [], from_lines: [] },
    });
    fireEvent.click(screen.getByRole("tab", { name: "메모" }));
    expect(screen.getByText("다른 사람이 남긴 메모.")).toBeTruthy();

    // AI 요약
    socket().emit({
      type: "ai.batch",
      seq: 1,
      agendas: [{ ...aiAgenda, lines: [{ line_id: "b1", track: "ai", order: 1, text: "배치가 쓴 줄.", author: null, at_ms: null, evidence: [], from_lines: [] }] }],
    });
    fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));
    expect(screen.getByText("배치가 쓴 줄.")).toBeTruthy();

    // 다시 묻지 않는다 — 오는 것을 받는다
    const reads = vi.mocked(api.readMeeting).mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(vi.mocked(api.readMeeting).mock.calls.length).toBe(reads);
  });

  it("마이크는 ready 뒤에 열리고 청크가 그 연결로 올라간다", async () => {
    renderLive(true);
    await screen.findByText("DB ax 전략");
    (await openedSocket()).accept();
    // ready 전에는 오디오를 보내지 않는다 — 보내면 서버가 버린다
    expect(window.navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();

    socket().emit({ type: "ready", meetingStartedAt: "2026-09-08T06:30:00Z", latestBatchSeq: 0, speakerCount: 1 });
    await waitFor(() => expect(FakeRecorder.instances).toHaveLength(1));
    expect(FakeRecorder.lastMime).toBe("audio/webm;codecs=opus");

    const buffer = new ArrayBuffer(8);
    await act(async () => {
      FakeRecorder.instances[0].ondataavailable?.({ data: { size: 8, arrayBuffer: async () => buffer } });
      await Promise.resolve();
    });
    await waitFor(() => expect(socket().audioChunks).toHaveLength(1));
  });

  it("마이크를 거부해도 화면이 멈추지 않고 상태 줄로 알린다", async () => {
    vi.mocked(window.navigator.mediaDevices.getUserMedia).mockRejectedValue(new Error("denied"));
    await connect(true);
    expect(await screen.findByText(/마이크를 쓸 수 없습니다/)).toBeTruthy();
    // 스트림은 그대로 산다
    socket().emit({ type: "transcript.final", item: { id: "f1", speakerLabel: "화자 1", atMs: 62_000, endMs: 65_000, content: "계속 받습니다." } });
    fireEvent.click(screen.getByRole("tab", { name: "스크립트" }));
    expect(screen.getByText("계속 받습니다.")).toBeTruthy();
  });

  it("잠정 발화는 교체되고 확정 발화는 쌓인다", async () => {
    await connect(true);
    fireEvent.click(screen.getByRole("tab", { name: "스크립트" }));

    socket().emit({ type: "transcript.partial", segments: [{ speakerLabel: "화자 1", atMs: 61_000, text: "토큰 수요가" }] });
    expect(screen.getByText("토큰 수요가")).toBeTruthy();
    socket().emit({ type: "transcript.partial", segments: [{ speakerLabel: "화자 1", atMs: 61_000, text: "토큰 수요가 는다" }] });
    expect(screen.queryByText("토큰 수요가")).toBeNull();
    expect(screen.getByText("토큰 수요가 는다")).toBeTruthy();

    socket().emit({ type: "transcript.final", item: { id: "f1", speakerLabel: "화자 1", atMs: 61_000, endMs: 64_000, content: "토큰 수요가 는다." } });
    expect(screen.getByText("토큰 수요가 는다.")).toBeTruthy();
    // 확정이 서면 그 잠정은 사라진다 — 잠정은 저장되지 않는다
    expect(screen.queryByText("토큰 수요가 는다")).toBeNull();
    socket().emit({ type: "transcript.final", item: { id: "f2", speakerLabel: "화자 2", atMs: 70_000, endMs: 72_000, content: "대응은 두 축이다." } });
    expect(screen.getByText("토큰 수요가 는다.")).toBeTruthy();
    expect(screen.getByText("대응은 두 축이다.")).toBeTruthy();
    // 눈금은 회의 경과이고 (D50) 화자는 익명 라벨뿐이다 — 61초면 01:01
    expect(screen.getAllByText("01:01").length).toBeGreaterThan(0);
    // 진행 표시 띠의 경과 시간도 같은 규칙으로 읽는다 — 이제 둘이 한 표기다
    const liveBar = document.querySelector(".scax-live-bar") as HTMLElement;
    expect(within(liveBar).getByText(/^\d+:\d{2}(:\d{2})?$/)).toBeTruthy();
    expect(screen.getByText("화자 2")).toBeTruthy();
  });

  it("회의 중에도 안건을 세운다 — 세운 안건이 곧 메모의 대상이 된다", async () => {
    await connect(true);
    const created = { ...agenda, agenda_id: "a2", order: 2, title: "협력 범위", source: "manual" as const, lines: [] };
    vi.mocked(api.addMeetingAgenda).mockResolvedValue(created);
    vi.mocked(api.readMeeting).mockResolvedValue({ meeting: meeting({ can_write_memo: true }), agendas: [agenda, created] });

    // 드롭다운 바닥의 「새 안건」이 같은 칸을 제목 받는 자리로 바꾼다
    const target = () => screen.getByRole("button", { name: meetingScreen.agenda });
    expect(target().textContent).toContain("안건 1");
    fireEvent.click(target());
    fireEvent.click(await screen.findByRole("button", { name: "새 안건" }));
    fireEvent.change(screen.getByLabelText("안건을 적으세요"), { target: { value: "협력 범위" } });
    fireEvent.click(screen.getByRole("button", { name: "안건 추가" }));
    await waitFor(() => expect(api.addMeetingAgenda).toHaveBeenCalledWith("m1", "협력 범위"));

    // 세운 안건이 대상이 되고 칸은 메모로 돌아온다
    await waitFor(() => expect(target().textContent).toContain("안건 2"));
    expect(screen.getByLabelText("메모를 남기세요")).toBeTruthy();

    vi.mocked(api.addMeetingMemoLine).mockResolvedValue({
      line_id: "m1",
      track: "memo",
      order: 1,
      text: "그 안건에 붙는 메모.",
      author: "1",
      at_ms: 90_000,
      evidence: [], from_lines: [],
    });
    fireEvent.change(screen.getByLabelText("메모를 남기세요"), { target: { value: "그 안건에 붙는 메모." } });
    fireEvent.click(screen.getByRole("button", { name: "기록" }));
    await waitFor(() => expect(api.addMeetingMemoLine).toHaveBeenCalledWith("m1", "a2", "그 안건에 붙는 메모."));
  });

  it("보는 창은 회의 중에 선 안건을 프레임으로 받는다", async () => {
    await connect(false);
    expect(screen.getByText("안건 1. 토큰 수요 전망")).toBeTruthy();

    socket().emit({
      type: "agenda.added",
      agenda: { ...agenda, agenda_id: "a2", order: 2, title: "협력 범위", source: "manual", lines: [], todos: [] },
    });
    // 상세를 다시 읽지 않아도 목록에 선다
    expect(screen.getByText("안건 2. 협력 범위")).toBeTruthy();
  });

  it("화자는 번호로 오고 화면은 「화자 N」으로 읽는다 — 확정도 잠정도 같다", async () => {
    await connect(true);
    fireEvent.click(screen.getByRole("tab", { name: "스크립트" }));

    socket().emit({ type: "transcript.final", item: { id: "f1", speakerLabel: "1", atMs: 61_000, endMs: 64_000, content: "확정된 말." } });
    socket().emit({ type: "transcript.partial", segments: [{ speakerLabel: "2", atMs: 70_000, text: "아직 굳지 않은 말" }] });

    const script = screen.getByRole("list", { name: "스크립트" });
    expect(within(script).getByText("화자 1")).toBeTruthy();
    expect(within(script).getByText("화자 2")).toBeTruthy();
    // 번호만 덩그러니 서지 않는다
    expect(within(script).queryByText("1")).toBeNull();
    expect(within(script).queryByText("2")).toBeNull();
  });

  it("ai.batch 는 AI 트랙을 통째로 갈아 끼우고 읽던 자리를 지킨다", async () => {
    await connect(true);
    fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));
    expect(screen.getByText("회의 전에 AI 가 적어 둔 줄.")).toBeTruthy();

    /* 읽던 자리를 지키는 칸은 «회의록 본문» 이다 — `noteScroll` 이 가리키는 그 노드다.
       예전에는 `.meeting-scroll`(첨부 칸의 스크롤 상자)을 잡고 있었는데, 그 칸은 배치가
       건드리지도 않으므로 scrollTop 이 그대로인 것이 당연했다. 첨부 칸이 시안대로 다시
       그려지며 그 클래스가 사라져 이 자리가 드러났다 — 이제 실제로 지키는지를 본다. */
    const pane = document.querySelector(".scax-note__body") as HTMLElement;
    Object.defineProperty(pane, "scrollHeight", { configurable: true, value: 900 });
    Object.defineProperty(pane, "clientHeight", { configurable: true, value: 300 });
    pane.scrollTop = 120;

    socket().emit({
      type: "ai.batch",
      seq: 1,
      agendas: [
        {
          ...aiAgenda,
          agenda_id: "batch-1",
          title: "토큰 수요 전망",
          lines: [{ line_id: "b1", track: "ai", order: 1, text: "배치가 새로 쓴 줄.", author: "AI", at_ms: null, evidence: [], from_lines: [] }],
        },
      ],
    });

    // 통째 교체 — 앞 회차의 줄은 남지 않는다
    expect(screen.getByText("배치가 새로 쓴 줄.")).toBeTruthy();
    expect(screen.queryByText("회의 전에 AI 가 적어 둔 줄.")).toBeNull();
    // 바닥에 붙어 있지 않았으므로 읽던 자리를 지킨다
    expect(pane.scrollTop).toBe(120);
  });

  it("메모 입력 칸은 서버가 연 사람에게만 선다 (can_write_memo)", async () => {
    await watch();
    expect(screen.queryByLabelText("메모를 남기세요")).toBeNull();
    expect(screen.queryByRole("button", { name: "기록" })).toBeNull();
    cleanup();

    await connect(true);
    expect(screen.getByLabelText("메모를 남기세요")).toBeTruthy();
  });

  it("메모는 돌아온 줄만 붙는다 — 실패하면 친 것을 그대로 두고 알린다", async () => {
    await connect(true);
    vi.mocked(api.addMeetingMemoLine).mockRejectedValueOnce(new Error("boom"));
    fireEvent.change(screen.getByLabelText("메모를 남기세요"), { target: { value: "분기별로 다시 뽑기로." } });
    fireEvent.click(screen.getByRole("button", { name: "기록" }));
    expect(await screen.findByText("저장하지 못했습니다. 다시 시도하고 있습니다.")).toBeTruthy();
    expect((screen.getByLabelText("메모를 남기세요") as HTMLInputElement).value).toBe("분기별로 다시 뽑기로.");

    vi.mocked(api.addMeetingMemoLine).mockResolvedValueOnce({
      line_id: "m1",
      track: "memo",
      order: 1,
      text: "분기별로 다시 뽑기로.",
      author: "1",
      at_ms: 120_000,
      evidence: [], from_lines: [],
    });
    fireEvent.click(screen.getByRole("button", { name: "기록" }));
    await waitFor(() => expect(api.addMeetingMemoLine).toHaveBeenCalledWith("m1", "a1", "분기별로 다시 뽑기로."));
    expect(await screen.findByText("분기별로 다시 뽑기로.")).toBeTruthy();
    // 스크립트에는 서지 않는다 (사용자 결정) — 전사만 그 자리에 선다
    fireEvent.click(screen.getByRole("tab", { name: "스크립트" }));
    expect(screen.queryByRole("list", { name: "스크립트" })).toBeNull();
    expect(screen.getByText("아직 원문이 없습니다.")).toBeTruthy();
  });

  it("메모는 「메모」 탭에만 선다 — 작성자가 없어도 스크립트가 흔들리지 않는다", async () => {
    await connect(true);
    vi.mocked(api.addMeetingMemoLine).mockResolvedValueOnce({
      line_id: "m9",
      track: "memo",
      order: 1,
      text: "작성자 없이 온 줄.",
      author: null,
      at_ms: 130_000,
      evidence: [], from_lines: [],
    });
    fireEvent.change(screen.getByLabelText("메모를 남기세요"), { target: { value: "작성자 없이 온 줄." } });
    fireEvent.click(screen.getByRole("button", { name: "기록" }));
    expect(await screen.findByText("작성자 없이 온 줄.")).toBeTruthy();

    // 스크립트는 전사만 낸다 — 확정 발화가 오면 그것만 선다
    socket().emit({ type: "transcript.final", item: { id: "f1", speakerLabel: "화자 1", atMs: 61_000, endMs: 64_000, content: "전사만 선다." } });
    fireEvent.click(screen.getByRole("tab", { name: "스크립트" }));
    const script = screen.getByRole("list", { name: "스크립트" });
    expect(within(script).getByText("전사만 선다.")).toBeTruthy();
    expect(within(script).queryByText("작성자 없이 온 줄.")).toBeNull();
    expect(within(script).getAllByRole("listitem")).toHaveLength(1);
  });

  it("[회의 종료]는 연결을 닫고 「정리 중」 화면으로 넘긴다", async () => {
    await connect(true);
    vi.mocked(api.endMeeting).mockResolvedValue({ meeting: meeting({ status: "summarizing" }), agendas: [agenda] });
    vi.mocked(api.readMeeting).mockResolvedValue({ meeting: meeting({ status: "summarizing" }), agendas: [agenda] });
    fireEvent.click(screen.getByRole("button", { name: "회의 종료" }));
    await waitFor(() => expect(api.endMeeting).toHaveBeenCalledWith("m1"));
    // 서버가 정상 종료로 닫는다 — 오류 프레임을 앞세우지 않는다
    socket().serverClose(1000, "");
    await waitFor(() => expect(screen.getByText("정리하는 중")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "회의 종료" })).toBeNull();
    // 다시 붙지 않는다
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("ready 전에 닫혀도 「연결하는 중」에 머무르지 않는다 — 끊김으로 드러낸다", async () => {
    renderLive(true);
    await screen.findByText("DB ax 전략");
    const opened = await openedSocket();
    opened.accept();
    expect(screen.getByText("연결하는 중")).toBeTruthy();

    // 붙지 못한 것도 끊긴 것이다. 서버가 말을 안 남기면 닫힌 코드가 그대로 사유다
    opened.serverClose(1006, "");
    expect(await screen.findByText(/연결이 끊겼습니다\. 1006/)).toBeTruthy();
    expect(screen.queryByText("연결하는 중")).toBeNull();
    // 다시 붙지 않는다
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("업스트림 자리가 찼으면 구독으로 다시 붙어 갱신은 그대로 받는다", async () => {
    await connect(true);
    await waitFor(() => expect(FakeRecorder.instances).toHaveLength(1));
    const recordersBefore = FakeRecorder.instances.length;
    socket().serverClose(4409, "meeting_stream_active");
    // 첫 연결의 마이크는 닫힌다
    expect(FakeRecorder.instances[0].state).toBe("inactive");

    // 역할만 바꿔 다시 붙는다
    await waitFor(() => expect(FakeSocket.instances).toHaveLength(2));
    socket().accept();
    expect(socket().auth).toEqual({ type: "auth", role: "subscribe" });
    // 붙어 있어도 그 사실은 계속 말한다
    expect(await screen.findByText("다른 창에서 진행 중입니다.")).toBeTruthy();

    socket().emit({ type: "ready", meetingStartedAt: "2026-09-08T06:30:00Z", latestBatchSeq: 0, speakerCount: 2 });
    // 오디오도 메모도 [회의 종료]도 이 창의 것이 아니다
    await waitFor(() => expect(socket().sent.length).toBeGreaterThan(0));
    expect(FakeRecorder.instances).toHaveLength(recordersBefore);
    expect(socket().audioChunks).toHaveLength(0);
    expect(screen.queryByLabelText("메모를 남기세요")).toBeNull();
    expect(screen.queryByRole("button", { name: "회의 종료" })).toBeNull();

    // 갱신은 그대로 받는다
    socket().emit({ type: "transcript.final", item: { id: "f1", speakerLabel: "1", atMs: 61_000, endMs: 64_000, content: "구독으로도 받는다." } });
    fireEvent.click(screen.getByRole("tab", { name: "스크립트" }));
    expect(screen.getByText("구독으로도 받는다.")).toBeTruthy();
  });

  it("중간에 들어와도 이미 적재된 원문부터 보인다 — 겹치는 줄은 한 번만 선다", async () => {
    vi.mocked(api.readMeetingTranscript).mockResolvedValue({
      items: [
        { id: "b1", speakerLabel: "화자 1", atMs: 30_000, endMs: 35_000, content: "들어오기 전에 오간 말." },
        { id: "b2", speakerLabel: "화자 2", atMs: 45_000, endMs: 50_000, content: "그 다음 말." },
      ],
      memos: [],
    });
    await connect(true);
    fireEvent.click(screen.getByRole("tab", { name: "스크립트" }));
    const script = await screen.findByRole("list", { name: "스크립트" });
    expect(within(script).getByText("들어오기 전에 오간 말.")).toBeTruthy();
    expect(within(script).getByText("그 다음 말.")).toBeTruthy();

    // 뒤이어 오는 확정 발화가 그 뒤에 붙는다
    socket().emit({ type: "transcript.final", item: { id: "b3", speakerLabel: "화자 1", atMs: 61_000, endMs: 64_000, content: "붙어서 오는 말." } });
    expect(within(script).getAllByRole("listitem")).toHaveLength(3);

    // 서버가 이미 준 줄이 스트림으로 다시 와도 두 번 서지 않는다
    socket().emit({ type: "transcript.final", item: { id: "b2", speakerLabel: "화자 2", atMs: 45_000, endMs: 50_000, content: "그 다음 말." } });
    expect(within(script).getAllByRole("listitem")).toHaveLength(3);
  });

  it("끊기면 사유를 그대로 내고 다시 붙지 않는다", async () => {
    await connect(true);
    socket().emit({ type: "error", code: "meeting_stream_disconnected", reason: "provider 연결이 끊겼습니다" });
    socket().serverClose(1006, "");
    expect(await screen.findByText(/연결이 끊겼습니다\. provider 연결이 끊겼습니다/)).toBeTruthy();
    expect(FakeSocket.instances).toHaveLength(1);
    // 멈췄다 잇는 조작을 두지 않는다
    expect(screen.queryByRole("button", { name: /다시 연결|일시정지|재개/ })).toBeNull();
  });

  it("인증이 죽으면 로그인으로 돌려보낸다", async () => {
    const { onSessionLost } = await connect(true);
    socket().serverClose(4401, "");
    await waitFor(() => expect(onSessionLost).toHaveBeenCalled());
  });

  it("상단에 AI 채팅 입력도 알림도 없다", async () => {
    await connect(true);
    expect(screen.queryByRole("button", { name: /알림/ })).toBeNull();
    expect(screen.queryByPlaceholderText(/무엇이든/)).toBeNull();
    // 회의 중에 자료를 붙이거나 떼지 않는다 (X-117)
    expect(screen.queryByRole("button", { name: /자료 첨부/ })).toBeNull();
  });

  it("회의가 끝나면 원문을 다시 읽는다 — 서버가 갈아 끼운 글만 선다 (D2)", async () => {
    vi.mocked(api.readMeetingTranscript)
      .mockResolvedValueOnce({
        items: [
          { id: "p1", speakerLabel: "1", atMs: 1_000, endMs: 4_000, content: "진행 중에 적재된 첫 줄." },
          { id: "p2", speakerLabel: "2", atMs: 5_000, endMs: 8_000, content: "진행 중에 적재된 둘째 줄." },
        ],
        memos: [],
      })
      .mockResolvedValue({
        items: [
          { id: "r1", speakerLabel: "1", atMs: 1_000, endMs: 4_000, content: "재전사가 낸 첫 줄." },
          { id: "r2", speakerLabel: "2", atMs: 5_000, endMs: 8_000, content: "재전사가 낸 둘째 줄." },
          { id: "r3", speakerLabel: "1", atMs: 9_000, endMs: 12_000, content: "재전사가 낸 셋째 줄." },
        ],
        memos: [],
      });

    await connect(false);
    socket().emit({
      type: "transcript.final",
      item: { id: "f1", speakerLabel: "1", atMs: 9_000, endMs: 12_000, content: "스트림으로 온 줄." },
    });
    fireEvent.click(screen.getByRole("tab", { name: "스크립트" }));
    expect(screen.getByText("진행 중에 적재된 첫 줄.")).toBeTruthy();
    expect(screen.getByText("스트림으로 온 줄.")).toBeTruthy();

    // 회의가 끝난다 — 스트림이 1000 으로 닫히고 상세를 다시 읽는다
    vi.mocked(api.readMeeting).mockResolvedValue({
      meeting: meeting({ can_write_memo: false, status: "done" }),
      agendas: [agenda],
    });
    socket().serverClose(1000);

    // 상태가 넘어간 그 마디에서 원문을 다시 읽는다 — 서버가 통째로 갈아 끼운 글이 온다
    expect(await screen.findByText("재전사가 낸 셋째 줄.")).toBeTruthy();
    expect(screen.getByText("재전사가 낸 첫 줄.")).toBeTruthy();
    expect(vi.mocked(api.readMeetingTranscript).mock.calls.length).toBeGreaterThanOrEqual(2);
    // 진행 중에 본 글도, 스트림으로 쌓인 줄도 남지 않는다 — 끝난 회의의 원문은 서버 것 하나다
    expect(screen.queryByText("진행 중에 적재된 첫 줄.")).toBeNull();
    expect(screen.queryByText("스트림으로 온 줄.")).toBeNull();
  });

  it("회의 중에도 줄의 시간 칩을 눌러 스크립트의 그 자리로 간다 (D3)", async () => {
    const evidenced: MeetingAgenda = {
      // 근거 칩이 붙는 줄은 AI 줄이다 — 줄은 «자기 벌의» 안건에만 매달린다 (§4.2-9)
      ...aiAgenda,
      lines: [
        {
          line_id: "l7",
          track: "ai",
          order: 1,
          text: "AI 가 회의 중에 적은 줄.",
          author: null,
          at_ms: null,
          evidence: [{ start_ms: 120_000, end_ms: 150_000 }], from_lines: [],
        },
      ],
    };
    await connect(false, {}, [evidenced]);
    socket().emit({
      type: "transcript.final",
      item: { id: "f1", speakerLabel: "1", atMs: 130_000, endMs: 133_000, content: "그 구간의 말." },
    });
    socket().emit({
      type: "transcript.final",
      item: { id: "f2", speakerLabel: "2", atMs: 200_000, endMs: 203_000, content: "다른 구간의 말." },
    });

    fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));
    // 회의가 끝나기를 기다리지 않는다 — 시각이 보이는 줄은 그 자리에서 누를 수 있다
    fireEvent.click(screen.getByRole("button", { name: "02:00" }));

    const script = await screen.findByRole("list", { name: "스크립트" });
    const rows = within(script).getAllByRole("listitem");
    expect(rows.filter((row) => row.className.includes("active"))).toHaveLength(1);
    expect(rows.find((row) => row.className.includes("active"))?.textContent).toContain("그 구간의 말.");
  });

  it("배치가 실어 온 후속 업무 후보가 「AI 요약」 탭 안건 밑에 선다 — 읽기만 한다 (D46)", async () => {
    await connect(false);
    fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));
    // 배치가 오기 전이고 상세에도 잠정 후보가 없다 — 구획은 서고 없다고 말한다
    expect(screen.getByText(meetingScreen.todos)).toBeTruthy();
    expect(screen.getByText(meetingScreen.noTodos)).toBeTruthy();

    socket().emit({
      type: "ai.batch",
      seq: 1,
      agendas: [
        {
          ...aiAgenda,
          lines: [{ line_id: "b1", track: "ai", order: 1, text: "배치가 쓴 줄.", author: null, at_ms: null, evidence: [], from_lines: [] }],
          todos: [
            todo({ todo_id: "p1", title: "전망치 다시 뽑기", due_candidate: "2026-09-15" }),
            todo({ todo_id: "p2", title: "협력 범위 정리" }),
          ],
        },
      ],
    });

    expect(screen.getByText("전망치 다시 뽑기")).toBeTruthy();
    expect(screen.getByText("2026-09-15")).toBeTruthy();
    // 기한 후보가 없는 줄은 그 자리를 비운다 — 없는 날짜를 지어내지 않는다
    expect(screen.getByText("협력 범위 정리")).toBeTruthy();
    // 회의 중에는 승격도 후보 빼기도 없다 — 종료 뒤 최종에서만 한다
    expect(screen.queryByRole("button", { name: meetingScreen.promote })).toBeNull();
    expect(screen.queryByRole("button", { name: meetingScreen.dropTodo })).toBeNull();
  });

  it("다음 배치가 후보를 통째로 갈아 끼운다 (D46)", async () => {
    await connect(false);
    fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));
    socket().emit({
      type: "ai.batch",
      seq: 1,
      agendas: [{ ...aiAgenda, todos: [todo({ todo_id: "p1", title: "1회차가 낸 후보" })] }],
    });
    expect(screen.getByText("1회차가 낸 후보")).toBeTruthy();

    socket().emit({
      type: "ai.batch",
      seq: 2,
      agendas: [{ ...aiAgenda, todos: [todo({ todo_id: "p2", title: "2회차가 낸 후보" })] }],
    });
    // 줄 id 를 붙들지 않는다 — 이번 회차가 낸 것이 곧 전부다
    expect(screen.getByText("2회차가 낸 후보")).toBeTruthy();
    expect(screen.queryByText("1회차가 낸 후보")).toBeNull();
  });

  it("배치 전에 들어온 참여자는 상세의 잠정 후보로 선다 — 메모 탭에는 없다 (D46)", async () => {
    const loaded: MeetingAgenda = {
      // 잠정 후보는 AI 벌과 최종 벌에만 선다 (§4.0-6)
      ...aiAgenda,
      todos: [
        todo({ todo_id: "p1", title: "이미 적재된 잠정 후보", due_candidate: "2026-09-15" }),
        // 최종(provisional=false)은 회의 중에 서지 않는다 — 종료 뒤 화면의 것이다
        todo({ todo_id: "t1", title: "최종에서만 서는 줄", provisional: false }),
      ],
    };
    await connect(false, {}, [loaded]);

    fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));
    expect(screen.getByText("이미 적재된 잠정 후보")).toBeTruthy();
    expect(screen.queryByText("최종에서만 서는 줄")).toBeNull();
    expect(screen.queryByRole("button", { name: meetingScreen.promote })).toBeNull();

    // 메모 탭은 회의 중에 오간 말만 낸다 — 후속 업무 자리 자체가 없다
    fireEvent.click(screen.getByRole("tab", { name: "메모" }));
    expect(screen.queryByText("이미 적재된 잠정 후보")).toBeNull();
    expect(screen.queryByText(meetingScreen.todos)).toBeNull();
  });
});

/* ════════════════════════════════════════════════════════════════════════════
   회의 «중» 화면 다섯 곳 — 사용자가 실물 화면을 보고 발주한 것 (2026-09-14).
   시안 21·22·23·24 · 17 과 SPEC §5.7 · §6 · §7 이 근거다.
   ════════════════════════════════════════════════════════════════════════════ */

/** AI 가 아직 아무것도 안 낸 안건 — 사람이 쓴 제목만 있고 AI 줄도 잠정 후보도 없다. */
const humanOnlyAgenda: MeetingAgenda = { ...agenda, lines: [], todos: [] };

describe("SCR-106 「진행 중」 — 회의 중 화면 다섯 곳", () => {
  /*
   * 1. 메모 입력줄 — 시안 22 는 칸이 상세 폭을 거의 다 쓰고 오른쪽 «밖» 에 보라 [기록]이 선다.
   *    예전에는 footer 가 둘이라 안쪽 것이 자라지 않았고, 칸이 왼쪽에 짧게 몰려 있었다(현재 화면 21).
   *    폭은 jsdom 이 못 재므로 «자라는 자리에 있는가» 와 «한 줄에 함께 서는가» 를 본다.
   */
  it("메모 칸은 그 줄을 채우고 [기록]은 칸 밖 오른쪽에 선다 — 안건 선택과 [+ 새 안건]은 그대로다", async () => {
    await connect(true);

    const foot = document.querySelector(".scax-note__composer") as HTMLElement;
    // 칸을 감싸는 껍데기가 그 줄의 «자라는» 자식이다 — footer 가 둘이던 때는 이 자리가 없었다
    const shell = foot.querySelector(".scax-memo-composer") as HTMLElement;
    expect(shell).toBeTruthy();
    expect(foot.querySelectorAll("footer")).toHaveLength(0);

    // 칸과 [기록]은 «같은 줄» 이고, [기록]은 칸 밖이다
    const row = shell.querySelector(".composer-row") as HTMLElement;
    const field = row.querySelector(".composer") as HTMLElement;
    const record = within(row).getByRole("button", { name: meetingScreen.recordMemo });
    expect(field.contains(record)).toBe(false);
    expect(row.contains(record)).toBe(true);

    // SPEC §6-4 의 두 자리는 칸 «안» 에 그대로 있다 — 대상 안건과 [+ 새 안건]
    expect(within(field).getByRole("button", { name: /안건/ })).toBeTruthy();
    fireEvent.click(within(field).getByRole("button", { name: /안건/ }));
    expect(await screen.findByRole("button", { name: meetingScreen.newAgenda })).toBeTruthy();
  });

  it("[기록]은 빈 칸에서 눌리지 않고, 저장에 실패하면 친 것이 칸에 그대로 남는다 (§6-7)", async () => {
    await connect(true);
    const record = () => screen.getByRole("button", { name: meetingScreen.recordMemo });
    expect(record().hasAttribute("disabled")).toBe(true);

    fireEvent.change(screen.getByLabelText("메모를 남기세요"), { target: { value: "적던 메모." } });
    expect(record().hasAttribute("disabled")).toBe(false);

    vi.mocked(api.addMeetingMemoLine).mockRejectedValue(new Error("저장 실패"));
    fireEvent.click(record());
    expect(await screen.findByText(meetingScreen.memoSaveFailed)).toBeTruthy();
    expect((screen.getByLabelText("메모를 남기세요") as HTMLInputElement).value).toBe("적던 메모.");
  });

  /*
   * 2. AI 요약 대기 — AI 트랙이 비어 있는 동안 사람이 쓴 안건 제목과 빈 후보 상자를
   *    AI 결과인 척 세우고 있었다(현재 화면 24).
   */
  it("AI 트랙이 비면 «곧 생성됩니다» 한 줄만 낸다 — 사람이 쓴 안건을 대신 세우지 않는다", async () => {
    await connect(false, {}, [humanOnlyAgenda]);
    fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));

    expect(screen.getByText(meetingScreen.aiSummaryPending)).toBeTruthy();
    expect(screen.queryByText(/토큰 수요 전망/)).toBeNull();
    // 빈 「다음 할 일」 상자도 서지 않는다 — 낼 값이 없는데 자리를 먼저 그리지 않는다
    expect(screen.queryByText(meetingScreen.todos)).toBeNull();

    // 「메모」 탭은 그대로다 — 적던 자리가 이 판정에 끌려가지 않는다
    fireEvent.click(screen.getByRole("tab", { name: "메모" }));
    expect(screen.getByText(/토큰 수요 전망/)).toBeTruthy();
    expect(screen.queryByText(meetingScreen.aiSummaryPending)).toBeNull();
  });

  it("배치가 오면 그 자리가 AI 트랙으로 바뀐다", async () => {
    await connect(false, {}, [humanOnlyAgenda]);
    fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));
    expect(screen.getByText(meetingScreen.aiSummaryPending)).toBeTruthy();

    socket().emit({
      type: "ai.batch",
      seq: 1,
      agendas: [
        {
          ...aiAgenda,
          agenda_id: "batch-1",
          title: "배치가 세운 안건",
          lines: [{ line_id: "b1", track: "ai", order: 1, text: "배치가 쓴 줄.", author: "AI", at_ms: null, evidence: [], from_lines: [] }],
        },
      ],
    });

    expect(screen.queryByText(meetingScreen.aiSummaryPending)).toBeNull();
    expect(screen.getByText("배치가 쓴 줄.")).toBeTruthy();
  });

  /*
   * 새로고침한 창에는 `stream.batch` 가 없다. 그 유무로만 판정하면 **이미 저장된 AI 요약이 숨는다** —
   * 판정은 상세 응답이 실어 온 AI 트랙 줄·잠정 후보까지 함께 본다.
   */
  it("배치 없이 들어와도 이미 저장된 AI 벌은 보인다 — 새로고침한 창이 그 경우다", async () => {
    /* ⚠ 이 검사의 전제가 v0.5.1 에서 바뀌었다. 예전에는 「AI 가 사람 안건에 줄을 붙이니
       `source` 로 거르면 안 된다」를 걸었는데, 이제 **줄은 자기 벌의 안건에만 매달린다** (§4.2-9)
       — AI 줄이 사람 벌 안건에 붙는 일 자체가 계약에서 사라졌다. 남은 뜻(배치 없이 들어와도
       이미 저장된 AI 요약이 보인다)은 그대로 걸고, 축을 `source` 에서 **벌** 로 옮겼다. */
    const summarized: MeetingAgenda = {
      ...aiAgenda,
      source: null,
      lines: [{ line_id: "s1", track: "ai", order: 1, text: "새로고침 전에 저장된 AI 줄.", author: "AI", at_ms: null, evidence: [], from_lines: [] }],
      todos: [],
    };
    await watch({}, [summarized]);

    fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));
    expect(screen.queryByText(meetingScreen.aiSummaryPending)).toBeNull();
    expect(screen.getByText("새로고침 전에 저장된 AI 줄.")).toBeTruthy();
  });

  it("줄은 없어도 잠정 후보가 남아 있으면 AI 트랙이 온 것이다", async () => {
    const onlyTodos: MeetingAgenda = {
      ...aiAgenda,
      lines: [],
      todos: [todo({ todo_id: "p1", title: "배치가 남긴 잠정 후보" })],
    };
    await watch({}, [onlyTodos]);

    fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));
    expect(screen.queryByText(meetingScreen.aiSummaryPending)).toBeNull();
    expect(screen.getByText("배치가 남긴 잠정 후보")).toBeTruthy();
  });

  /*
   * 3. 스크립트 가독성 — 레일이 342 라 세 칸을 가로로 세우면 본문에 남는 폭이 200 이 안 됐다
   *    (현재 화면 17). 칸을 없애고 줄을 쌓는다. 폭 때문에 글자를 줄이지 않았다.
   */
  it("발언은 「시각 · 화자」 한 줄 위, 본문이 그 아래 열 전체 폭이다", async () => {
    await connect(true);
    socket().emit({
      type: "transcript.final",
      item: { id: "t1", speakerLabel: "3", atMs: 15_000, endMs: 19_000, content: "이걸 보통 링키가 하고, 그리고 각 카카오톡 알림 쪽 계약 진행이고요." },
    });

    fireEvent.click(screen.getByRole("tab", { name: "스크립트" }));
    const script = await screen.findByRole("list", { name: "스크립트" });
    const line = within(script).getAllByRole("listitem")[0];

    // 메타 한 줄 안에 시각과 화자가 함께 선다
    const meta = line.querySelector(".scax-script-line__meta") as HTMLElement;
    expect(meta.querySelector(".scax-script-line__at")?.textContent).toBe("00:15");
    expect(meta.querySelector(".scax-script-line__who")?.textContent).toBe("화자 3");
    // 본문은 그 메타의 «형제» 다 — 같은 줄에 끼어 폭을 나눠 갖지 않는다
    const text = line.querySelector(".scax-script-line__text") as HTMLElement;
    expect(text.parentElement).toBe(line);
    expect(meta.contains(text)).toBe(false);
    expect(text.textContent).toBe("이걸 보통 링키가 하고, 그리고 각 카카오톡 알림 쪽 계약 진행이고요.");
  });

  it("잠정 줄과 근거로 가리킨 줄은 예전처럼 갈린다 — 모양이 바뀌어도 뜻은 그대로다", async () => {
    await connect(true);
    socket().emit({ type: "transcript.partial", segments: [{ speakerLabel: "1", atMs: 4_000, text: "아직 굳지 않은 말" }] });

    fireEvent.click(screen.getByRole("tab", { name: "스크립트" }));
    const script = await screen.findByRole("list", { name: "스크립트" });
    const tentative = within(script).getAllByRole("listitem")[0];
    expect(tentative.classList.contains("scax-script-line--tentative")).toBe(true);
    expect(tentative.classList.contains("scax-script-line--active")).toBe(false);
  });

  /*
   * 4. 진행 중 [자료 첨부] — 시안 23 에는 서 있지만 **계약이 막는다**:
   *    SPEC §5.7-4 「[자료 첨부]는 「진행 중」에 숨긴다 (X-117)」이고,
   *    backend 의 `decide_material_access` 도 `can_attach = ... and current is not IN_PROGRESS` 다.
   *    붙지 않을 단추를 세우지 않는다 — 이 검사가 그 자리를 지킨다.
   */
  it("「진행 중」에는 [자료 첨부]가 서지 않는다 — 시안에 있어도 계약이 막는다 (§5.7-4)", async () => {
    vi.mocked(api.readMeetingMaterials).mockResolvedValue([]);
    await connect(true);
    expect(screen.queryByRole("button", { name: /자료 첨부/ })).toBeNull();
  });

  /*
   * 5. [회의 종료] — **빨간 solid 를 지킨다** (2026-09-14 사용자 정정).
   *    시안 22 는 이 자리를 채움 없는 글자로 그렸지만, 회의를 닫는 것은 되돌릴 수 없는 걸음이라
   *    그만큼 눈에 띄어야 한다는 것이 사용자 판단이다. 시안을 따라 되돌리지 않도록 여기서 잠근다.
   */
  it("[회의 종료]는 빨간 solid 를 지킨다 — 누르면 하던 대로 회의를 닫는다", async () => {
    await connect(true);
    const end = screen.getByRole("button", { name: meetingScreen.end });
    expect(end.classList.contains("scax-button--solid-danger")).toBe(true);

    vi.mocked(api.endMeeting).mockResolvedValue({ meeting: meeting({ status: "summarizing" }), agendas: [agenda] });
    fireEvent.click(end);
    await waitFor(() => expect(api.endMeeting).toHaveBeenCalledWith("m1"));
  });

  it("보기만 하는 창에는 [회의 종료]가 서지 않는다 — 권한은 그대로다", async () => {
    await watch();
    expect(screen.queryByRole("button", { name: meetingScreen.end })).toBeNull();
  });
});

/* ════════════════════════════════════════════════════════════════════════════
   회의록이 **세 벌**이다 (SPEC-004 v0.5.1 §4.0 · §4.1-6 · §4.2-6 · §8-9).

   0.4.x 는 한 벌이었고 탭이 가른 것은 «줄» 뿐이었다. 이제 벌마다 자기 안건 목록을 갖는다 —
   서버가 세 벌을 한 `agendas` 배열에 모두 실어 보내므로, 화면이 `track` 으로 거르지 않으면
   같은 회의가 최대 3배로 보이고 「안건 1」이 셋 선다. 아래가 그 갈림 자체를 건다.
   ════════════════════════════════════════════════════════════════════════════ */
describe("회의록 세 벌 — 탭마다 자기 벌의 안건 목록", () => {
  /** 사람 벌과 AI 벌이 **다른 제목**을 든 회의 — 탭이 실제로 목록을 가르는지 보려면 달라야 한다. */
  const memoSide: MeetingAgenda = {
    ...agenda,
    agenda_id: "m-1",
    title: "사람이 적은 안건",
    lines: [{ line_id: "ml1", track: "memo", order: 1, text: "사람이 적은 줄.", author: "이건학", at_ms: 1_000, evidence: [], from_lines: [] }],
  };
  const aiSide: MeetingAgenda = {
    ...aiAgenda,
    agenda_id: "a-1",
    title: "AI 가 세운 안건",
    lines: [{ line_id: "al1", track: "ai", order: 1, text: "AI 가 적은 줄.", author: null, at_ms: null, evidence: [], from_lines: [] }],
  };

  it("메모 탭과 AI 탭의 **안건 목록이 다르다** — 줄만 갈리는 것이 아니다", async () => {
    await connect(true, {}, [memoSide, aiSide]);

    // 「메모」 탭 — 사람 벌의 안건과 그 안건의 줄만
    fireEvent.click(screen.getByRole("tab", { name: "메모" }));
    expect(screen.getByText(/사람이 적은 안건/)).toBeTruthy();
    expect(screen.queryByText(/AI 가 세운 안건/)).toBeNull();
    expect(screen.getByText("사람이 적은 줄.")).toBeTruthy();
    expect(screen.queryByText("AI 가 적은 줄.")).toBeNull();

    // 「AI 요약」 탭 — 목록이 통째로 바뀐다
    fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));
    expect(screen.getByText(/AI 가 세운 안건/)).toBeTruthy();
    expect(screen.queryByText(/사람이 적은 안건/)).toBeNull();
    expect(screen.getByText("AI 가 적은 줄.")).toBeTruthy();
    expect(screen.queryByText("사람이 적은 줄.")).toBeNull();
  });

  it("두 벌을 한 목록으로 합쳐 내지 않는다 — 「안건 1」이 둘 보이지 않는다", async () => {
    /* `order` 는 **벌 안에서** 1 부터 다시 매겨진다 (백엔드 보고 §4.5). 거르지 않으면
       같은 번호의 안건이 여러 개 서고, 그것이 가장 눈에 띄게 깨지던 자리다. */
    await connect(true, {}, [memoSide, aiSide]);
    fireEvent.click(screen.getByRole("tab", { name: "메모" }));
    expect(screen.getAllByText(/^안건 1\./)).toHaveLength(1);
  });

  it("AI 벌 안건에는 **편집 자리가 없다** — 사람이 언제도 고치지 못한다 (§4.1-6)", async () => {
    /* 서버 게이트가 AI 벌을 언제나 거짓으로 낸다. 화면이 그 값을 벌별로 읽는지 본다 —
       불리언 하나이던 때는 객체가 늘 truthy 라 **조용히 항상 열렸다.** */
    await connect(true, { can_edit_agendas: { memo: true, ai: false, final: false }, can_add_agenda: { memo: true, ai: false, final: false } }, [memoSide, aiSide]);

    fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));
    expect(screen.queryByRole("button", { name: meetingScreen.dropAgenda })).toBeNull();
    expect(screen.queryByLabelText(meetingScreen.agendaPlaceholder)).toBeNull();
    expect(screen.queryByRole("button", { name: meetingScreen.addAgenda })).toBeNull();
  });

  it("「진행 중」 사람 벌은 **더할 수는 있고 고칠 수는 없다** (§4.1-6)", async () => {
    /* 더하는 게이트와 고치는 게이트가 다르다 — 불리언 하나로는 낼 수 없던 자리다.
       이미 줄이 매달린 안건이 흔들리면 매달린 메모가 갈 곳을 잃으므로 고치기만 닫는다. */
    await connect(true, { can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: true, ai: false, final: false } }, [memoSide, aiSide]);

    fireEvent.click(screen.getByRole("tab", { name: "메모" }));
    /* 더하는 자리는 회의 중에도 산다 — 그 자리는 메모 칸 드롭다운 바닥의 [+ 새 안건]이다 (§6-4).
       드롭다운을 열어야 보이므로 사람이 하는 대로 연다. */
    fireEvent.click(screen.getByRole("button", { name: /안건/ }));
    expect(await screen.findByRole("button", { name: meetingScreen.newAgenda })).toBeTruthy();
    // 고치고 지우는 자리는 닫혀 있다
    expect(screen.queryByRole("button", { name: meetingScreen.dropAgenda })).toBeNull();
  });

  it("회의 중에는 [업무 생성]이 없다 — 승격은 종료 뒤 최종에서만 한다 (§9-3)", async () => {
    const withTodo: MeetingAgenda = { ...aiSide, todos: [todo({ todo_id: "p1", title: "회의 중 후보" })] };
    await connect(true, {}, [memoSide, withTodo]);

    fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));
    // 후보는 보이되 읽기만 한다 (D46)
    expect(screen.getByText("회의 중 후보")).toBeTruthy();
    expect(screen.queryByRole("button", { name: meetingScreen.promote })).toBeNull();
    expect(screen.queryByRole("button", { name: meetingScreen.dropTodo })).toBeNull();
  });

  it("결론 표시는 최종 벌에만 선다 — 회의 중에는 어느 탭에도 없다 (§4.0-5)", async () => {
    const concludedMemo: MeetingAgenda = { ...memoSide, concluded: true };
    const concludedAi: MeetingAgenda = { ...aiSide, concluded: true };
    await connect(true, {}, [concludedMemo, concludedAi]);

    for (const tab of ["메모", "AI 요약"]) {
      fireEvent.click(screen.getByRole("tab", { name: tab }));
      expect(screen.queryByText(meetingScreen.concluded)).toBeNull();
      expect(screen.queryByText(meetingScreen.notConcluded)).toBeNull();
    }
  });

  it("배치가 와도 **사람 벌은 그대로 있다** — 배치는 AI 벌만 싣는다", async () => {
    /* SSE `ai.batch` 의 페이로드가 «세 벌 전체» 에서 «AI 벌만» 으로 줄었다.
       받는 쪽이 그것으로 목록을 통째로 갈아 끼우면 **배치가 돌 때마다 사람 벌이 사라진다.**
       갈아 끼우기는 AI 탭에서만 돌고 사람 벌은 상세가 실어 온 것을 그대로 쓴다 — 그것을 건다. */
    await connect(true, {}, [memoSide, aiSide]);

    socket().emit({
      type: "ai.batch",
      seq: 1,
      agendas: [{ ...aiAgenda, agenda_id: "batch-1", title: "배치가 세운 안건", lines: [] }],
    });

    // AI 탭은 배치가 낸 것으로 갈린다
    fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));
    expect(screen.getByText(/배치가 세운 안건/)).toBeTruthy();
    expect(screen.queryByText(/AI 가 세운 안건/)).toBeNull();

    // 사람 벌은 배치가 건드리지 않는다 — 여기가 사라지던 자리다
    fireEvent.click(screen.getByRole("tab", { name: "메모" }));
    expect(screen.getByText(/사람이 적은 안건/)).toBeTruthy();
    expect(screen.getByText("사람이 적은 줄.")).toBeTruthy();
    expect(screen.queryByText(/배치가 세운 안건/)).toBeNull();
  });

  it("메모 대상 드롭다운에는 **사람 벌만** 뜬다 — AI 안건을 고르면 서버가 422 다", async () => {
    /* 메모는 사람 벌에만 매달린다 (§4.2-9). 합본을 넘기면 AI 안건이 후보로 서고, 고르면
       그 메모가 AI 벌 안건으로 나가 **422** 로 튕긴다 — 사람은 왜 안 되는지 알 길이 없다.
       실측에서 `MeetingDetailPage.tsx:1052` 가 안 거른 합본을 넘기던 자리다. */
    await connect(true, {}, [memoSide, aiSide]);
    fireEvent.click(screen.getByRole("tab", { name: "메모" }));

    fireEvent.click(screen.getByRole("button", { name: /안건/ }));
    const options = await screen.findAllByRole("option");
    // 사람 벌 안건 하나뿐이다 — 합본이면 「안건 1」·「안건 2」 둘이 선다
    expect(options).toHaveLength(1);
    expect(options[0].textContent).toContain("안건 1");
  });

  it("「진행 중」에도 **사람 벌 안건을 고치고 지운다** — 서버가 연 게이트를 화면이 닫지 않는다", async () => {
    /* 사용자 결정 2026-09-14 ①: 임시 두 벌은 임시로 다룬다. 전에는 화면이 `planned || cancelled`
       로 한 번 더 판단해서, 진행 중에 오타로 세운 안건(「장난치고 싶다」)이 영영 박제됐다.
       열지 말지는 서버의 `can_edit_agendas.memo` 하나가 정한다. */
    await connect(true, { can_edit_agendas: { memo: true, ai: false, final: false }, can_add_agenda: { memo: true, ai: false, final: false } }, [memoSide, aiSide]);

    fireEvent.click(screen.getByRole("tab", { name: "메모" }));
    expect(screen.getByRole("button", { name: meetingScreen.dropAgenda })).toBeTruthy();

    // AI 벌은 그대로 닫혀 있다 — 벌마다 다르다는 것이 이 대비다
    fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));
    expect(screen.queryByRole("button", { name: meetingScreen.dropAgenda })).toBeNull();
  });

  /* ──────────────────────────────────────────────────────────────────────────
     **안건 제목을 제자리에서 고친다** (2026-09-15 사용자 결정).

     계약 세 줄: 입력칸이 나타나지 않는다 · 테두리·바탕·그림자·둥근 모서리가 생기지 않는다 ·
     글자가 1px 도 움직이지 않는다. 그래서 `<input>` 으로 갈아 끼우지 않고 **글자를 이고 있던
     그 노드가 `contenteditable` 로 바뀐다** — 움직일 대상 자체가 없다.

     열지 말지는 서버의 `can_edit_agendas.memo` 하나가 정한다.
     ────────────────────────────────────────────────────────────────────────── */
  describe("안건 제목 제자리 편집", () => {
    const opened = { can_edit_agendas: { memo: true, ai: false, final: false }, can_add_agenda: { memo: true, ai: false, final: false } };
    /** 그 자리 — 닫혀 있으면 `button`, 열려 있으면 `textbox` 지만 **같은 노드**다. */
    const slot = () => screen.getByLabelText(meetingScreen.agendaTitleEdit);
    /** 사람이 치는 것 — `contenteditable` 에는 `value` 가 없고 글자가 곧 내용이다. */
    function type(element: HTMLElement, text: string) {
      element.textContent = text;
      fireEvent.input(element);
    }

    async function openMemoTitle() {
      await connect(true, opened, [memoSide, aiSide]);
      fireEvent.click(screen.getByRole("tab", { name: "메모" }));
      const element = slot();
      fireEvent.click(element);
      return element;
    }

    it("**같은 노드**가 편집으로 바뀐다 — 입력칸이 새로 나타나지 않는다", async () => {
      await connect(true, opened, [memoSide, aiSide]);
      fireEvent.click(screen.getByRole("tab", { name: "메모" }));
      const before = slot();
      expect(before.tagName).toBe("SPAN");
      expect(before.getAttribute("role")).toBe("button");

      fireEvent.click(before);

      // 노드가 «그대로» 다 — 갈아 끼웠다면 이 동일성이 깨진다
      expect(slot()).toBe(before);
      expect(before.isConnected).toBe(true);
      expect(before.getAttribute("contenteditable")).toBe("true");
      expect(before.getAttribute("role")).toBe("textbox");
      // 회의록 칸 «어디에도» 입력칸이 생기지 않았다
      const body = document.querySelector(".scax-note__body") as HTMLElement;
      expect(body.querySelector("input")).toBeNull();
      expect(body.querySelector("textarea")).toBeNull();
    });

    it("누르면 지금 글자를 들고 열린다 — 번호는 칸 밖에 남는다", async () => {
      const element = await openMemoTitle();
      expect(element.textContent).toBe("사람이 적은 안건");
      // 「안건 1.」은 고치는 자리 밖의 글자다
      expect(screen.getByText(/안건 1\./)).toBeTruthy();
    });

    it("`Enter` 로 저장한다 — 줄바꿈이 아니다", async () => {
      const element = await openMemoTitle();
      vi.mocked(api.updateMeetingAgenda).mockResolvedValue({ ...memoSide, title: "고친 제목" });
      type(element, "고친 제목");
      fireEvent.keyDown(element, { key: "Enter" });

      await waitFor(() => expect(api.updateMeetingAgenda).toHaveBeenCalledWith("m1", "m-1", { title: "고친 제목" }));
      /* 충돌 판정 자리를 두지 않으므로 읽은 시각을 싣지 않는다 (OQ-308) */
      expect(vi.mocked(api.updateMeetingAgenda).mock.calls[0][2]).not.toHaveProperty("expected_last_saved_at");
      // 칸이 닫힌다 — 같은 노드가 다시 글자가 된다
      await waitFor(() => expect(slot().getAttribute("contenteditable")).not.toBe("true"));
    });

    it("붙여넣기로 들어온 줄바꿈은 걷고 한 줄로 저장한다", async () => {
      const element = await openMemoTitle();
      vi.mocked(api.updateMeetingAgenda).mockResolvedValue({ ...memoSide });
      type(element, "두 줄로\n붙인 제목");
      fireEvent.keyDown(element, { key: "Enter" });

      await waitFor(() => expect(api.updateMeetingAgenda).toHaveBeenCalledWith("m1", "m-1", { title: "두 줄로 붙인 제목" }));
    });

    it("`Esc` 로 되돌아간다 — 친 글자를 버리고 요청도 안 나간다", async () => {
      const element = await openMemoTitle();
      type(element, "치다 만 글자");
      fireEvent.keyDown(element, { key: "Escape" });

      expect(api.updateMeetingAgenda).not.toHaveBeenCalled();
      await waitFor(() => expect(slot().getAttribute("contenteditable")).not.toBe("true"));
      expect(slot().textContent).toBe("사람이 적은 안건");
    });

    it("빈 값은 저장되지 않는다 — 이름을 지우는 자리가 아니다", async () => {
      const element = await openMemoTitle();
      type(element, "   ");
      fireEvent.keyDown(element, { key: "Enter" });

      expect(api.updateMeetingAgenda).not.toHaveBeenCalled();
      await waitFor(() => expect(slot().textContent).toBe("사람이 적은 안건"));
    });

    it("값이 그대로면 요청이 안 나간다", async () => {
      const element = await openMemoTitle();
      type(element, "사람이 적은 안건");
      fireEvent.keyDown(element, { key: "Enter" });

      expect(api.updateMeetingAgenda).not.toHaveBeenCalled();
    });

    it("저장이 실패하면 **원래 글자로 돌아온다**", async () => {
      const element = await openMemoTitle();
      vi.mocked(api.updateMeetingAgenda).mockRejectedValue(new Error("저장하지 못했습니다."));
      type(element, "못 갈 제목");
      fireEvent.keyDown(element, { key: "Enter" });

      await waitFor(() => expect(api.updateMeetingAgenda).toHaveBeenCalled());
      // 낙관 렌더가 없어서 되돌릴 것도 없다 — 서버가 말한 값이 그대로 선다
      await waitFor(() => expect(slot().textContent).toBe("사람이 적은 안건"));
    });

    it("제목 **오른쪽 빈 자리를 눌러도** 편집이 열린다 — 행이 과녁이다", async () => {
      /* 「안건 1. 제목」은 한 줄로 흐르는 자리라 글자(`span`)를 늘리면 둘 사이 간격이 바뀐다
         = 글자가 움직인다. 그래서 늘리지 않고 **부모 행(`h3`)이 클릭을 받는다.**
         jsdom 은 좌표로 「빈 자리」를 못 만들지만, 행을 누르는 것 자체는 만들 수 있다. */
      await connect(true, opened, [memoSide, aiSide]);
      fireEvent.click(screen.getByRole("tab", { name: "메모" }));
      const element = slot();
      const row = element.closest("h3") as HTMLElement;
      expect(row.classList.contains("scax-agenda-block__title--editable")).toBe(true);

      // 글자가 «아니라» 행을 누른다 — 예전에는 아무 일도 안 일어나던 자리다
      fireEvent.click(row);
      expect(slot()).toBe(element);
      expect(element.getAttribute("contenteditable")).toBe("true");
    });

    it("행을 눌러도 **레이아웃이 안 바뀐다** — 얹은 것은 커서 하나다", async () => {
      /* 행 과녁은 «핸들러 + `cursor:text`» 뿐이라 그릴 것이 없다. 높이·여백·배경을 주는
         클래스가 붙지 않는다는 것으로 그 사실을 건다 (jsdom 은 배치를 계산하지 않는다). */
      await connect(true, opened, [memoSide, aiSide]);
      fireEvent.click(screen.getByRole("tab", { name: "메모" }));
      const row = slot().closest("h3") as HTMLElement;
      const before = row.className;
      fireEvent.click(row);
      // 열려도 행의 클래스가 한 글자도 안 바뀐다
      expect(row.className).toBe(before);
      expect(row.getAttribute("style")).toBeNull();
    });

    it("행 과녁이 **[×] 를 먹지 않는다** — 단추 위는 제외한다", async () => {
      /* 안건 머리에는 「안건 빼기」 단추가 함께 선다. 행이 클릭을 받되 단추 위는 빠져야
         한다 — 지우려고 눌렀는데 편집이 열리면 지울 수가 없다. */
      await connect(true, opened, [memoSide, aiSide]);
      fireEvent.click(screen.getByRole("tab", { name: "메모" }));
      const drop = screen.getByRole("button", { name: meetingScreen.dropAgenda });

      fireEvent.click(drop);

      // 편집이 열리지 않았고, 지우기 확인이 대신 떴다
      expect(slot().getAttribute("contenteditable")).not.toBe("true");
      expect(screen.getByRole("alertdialog", { name: meetingScreen.agendaRemoveTitle })).toBeTruthy();
    });

    it("AI 벌 안건은 **눌러도 안 열린다** — 고치는 자리 자체가 없다", async () => {
      await connect(true, opened, [memoSide, aiSide]);
      fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));
      expect(screen.queryByLabelText(meetingScreen.agendaTitleEdit)).toBeNull();
      // 행 과녁도 서지 않는다 — 누를 수 있다는 커서조차 뜨지 않는다
      expect(document.querySelector(".scax-agenda-block__title--editable")).toBeNull();
      // 제목은 «글자로» 그대로 선다 — 못 고칠 뿐 안 보이는 것이 아니다
      expect(screen.getByText(/AI 가 세운 안건/)).toBeTruthy();
    });

    it("`can_edit_agendas.memo` 가 거짓이면 사람 벌도 안 열린다 — 서버가 정한다", async () => {
      await connect(true, { can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: true, ai: false, final: false } }, [memoSide, aiSide]);
      fireEvent.click(screen.getByRole("tab", { name: "메모" }));
      expect(screen.queryByLabelText(meetingScreen.agendaTitleEdit)).toBeNull();
      expect(screen.getByText(/사람이 적은 안건/)).toBeTruthy();
    });

    it("**테두리·바탕을 주는 클래스가 어느 상태에도 안 붙는다**", async () => {
      /* 「글자가 1px 도 안 움직인다」 자체는 jsdom 이 배치를 계산하지 않아 걸 수 없다 (보고서에 적었다).
         걸 수 있는 것은 **그 움직임을 만드는 원인**이다: DS 의 입력 껍데기 클래스가 붙으면
         48px 최소 높이·1px 테두리·좌우 안여백이 한꺼번에 따라 들어와 줄이 밀린다. */
      const shells = ["scax-textfield", "scax-field", "scax-textarea", "scax-composer", "meeting-line-edit"];
      await connect(true, opened, [memoSide, aiSide]);
      fireEvent.click(screen.getByRole("tab", { name: "메모" }));
      const element = slot();
      // 닫혔을 때
      expect(element.className).toBe("scax-inline-text");
      fireEvent.click(element);
      // 열렸을 때 — **클래스가 달라지지 않는다.** 모양을 바꿀 고리가 없다는 뜻이다
      expect(element.className).toBe("scax-inline-text");
      for (const shell of shells) expect(element.classList.contains(shell)).toBe(false);
    });
  });

  /* ──────────────────────────────────────────────────────────────────────────
     **메모 한 줄을 고치고 지운다** (백엔드 `6a9c41a` · 보고서 §4).
     쓰는 자리와 같은 주소 아래 같은 본문이다. **빈 줄로 지우지 않는다** — 빈 `text` 는 422 이고
     지우는 것은 `DELETE` 다. 게이트는 안건 [수정]·[삭제]와 **같은 값**(`can_edit_agendas.memo`)이다.
     ────────────────────────────────────────────────────────────────────────── */
  describe("메모 줄 고치기·지우기", () => {
    const opened = { can_edit_agendas: { memo: true, ai: false, final: false }, can_add_agenda: { memo: true, ai: false, final: false } };
    const memoSlot = () => screen.getByLabelText(meetingScreen.memoLineEdit);
    function type(element: HTMLElement, text: string) {
      element.textContent = text;
      fireEvent.input(element);
    }

    async function openMemoLine(over = opened) {
      await connect(true, over, [memoSide, aiSide]);
      fireEvent.click(screen.getByRole("tab", { name: "메모" }));
      const element = memoSlot();
      fireEvent.click(element);
      return element;
    }

    it("메모 줄을 **제자리에서** 고친다 — `PATCH …/lines/{lineId}` 로 간다", async () => {
      const element = await openMemoLine();
      vi.mocked(api.updateMeetingMemoLine).mockResolvedValue({ ...memoSide.lines[0], text: "고쳐 쓴 줄." });
      type(element, "고쳐 쓴 줄.");
      fireEvent.keyDown(element, { key: "Enter" });

      await waitFor(() => expect(api.updateMeetingMemoLine).toHaveBeenCalledWith("m1", "m-1", "ml1", "고쳐 쓴 줄."));
    });

    it("**빈 줄로 지우려 하지 않는다** — 빈 값은 요청 자체가 안 나간다 (§4: 빈 text 는 422)", async () => {
      const element = await openMemoLine();
      type(element, "   ");
      fireEvent.keyDown(element, { key: "Enter" });

      expect(api.updateMeetingMemoLine).not.toHaveBeenCalled();
      expect(api.removeMeetingMemoLine).not.toHaveBeenCalled();
    });

    it("지우는 것은 `DELETE` 다 — 확인을 묻지 않는다", async () => {
      await connect(true, opened, [memoSide, aiSide]);
      fireEvent.click(screen.getByRole("tab", { name: "메모" }));
      vi.mocked(api.removeMeetingMemoLine).mockResolvedValue(undefined);

      fireEvent.click(screen.getByRole("button", { name: meetingScreen.memoLineDrop }));

      await waitFor(() => expect(api.removeMeetingMemoLine).toHaveBeenCalledWith("m1", "m-1", "ml1"));
      // 확인 모달이 끼지 않는다 — 자기가 적은 임시 재료다
      expect(screen.queryByRole("alertdialog")).toBeNull();
    });

    it("**409 는 다시 읽어 맞춘다** — 게이트가 닫혔거나 남의 벌 줄이다", async () => {
      const element = await openMemoLine();
      const reads = vi.mocked(api.readMeeting).mock.calls.length;
      vi.mocked(api.updateMeetingMemoLine).mockRejectedValue(new api.ApiError(409, "conflict"));
      type(element, "못 갈 글자.");
      fireEvent.keyDown(element, { key: "Enter" });

      await waitFor(() => expect(vi.mocked(api.readMeeting).mock.calls.length).toBeGreaterThan(reads));
    });

    it("**404 도 다시 읽는다** — 권한 밖이 404 로 오는 것이 이 모듈의 계약이다 (403 을 기다리지 않는다)", async () => {
      await connect(true, opened, [memoSide, aiSide]);
      fireEvent.click(screen.getByRole("tab", { name: "메모" }));
      const reads = vi.mocked(api.readMeeting).mock.calls.length;
      vi.mocked(api.removeMeetingMemoLine).mockRejectedValue(new api.ApiError(404, "not found"));

      fireEvent.click(screen.getByRole("button", { name: meetingScreen.memoLineDrop }));

      await waitFor(() => expect(vi.mocked(api.readMeeting).mock.calls.length).toBeGreaterThan(reads));
    });

    it("**422 는 다시 읽지 않는다** — 서버는 그대로이고 알리기만 한다", async () => {
      const element = await openMemoLine();
      const reads = vi.mocked(api.readMeeting).mock.calls.length;
      vi.mocked(api.updateMeetingMemoLine).mockRejectedValue(new api.ApiError(422, "너무 깁니다."));
      type(element, "너무 긴 글자.");
      fireEvent.keyDown(element, { key: "Enter" });

      await waitFor(() => expect(api.updateMeetingMemoLine).toHaveBeenCalled());
      // 잠깐 기다려도 다시 읽지 않는다
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(vi.mocked(api.readMeeting).mock.calls.length).toBe(reads);
    });

    it("줄의 **빈 자리를 눌러도** 편집이 열린다 — 과녁이 글자 폭에 묶이지 않는다", async () => {
      /* 짧은 줄일수록 글자만한 과녁은 빗나간다. 칸(`.scax-note-line__text`)은 이미 늘어나 있고
         그 안의 글자만 인라인이라 좁았다 — 그 글자를 칸 너비만큼 넓혔다.
         jsdom 은 좌표로 「빈 자리를 눌렀다」를 만들 수 없으므로, **넓혔다는 사실**을 건다. */
      await connect(true, opened, [memoSide, aiSide]);
      fireEvent.click(screen.getByRole("tab", { name: "메모" }));
      const slot = memoSlot();
      expect(slot.classList.contains("scax-inline-text--fill")).toBe(true);
      // 과녁이 딛는 칸은 늘어나는 칸이다 — 그 둘이 붙어야 「빈 자리」가 눌린다
      expect(slot.closest(".scax-note-line__text")).not.toBeNull();
    });

    it("[×] 는 여전히 삭제로 동작한다 — 과녁을 넓혀도 빼는 자리를 먹지 않는다", async () => {
      /* 글자를 칸 너비로 넓히면 그 옆의 작은 단추를 덮기 쉽다. `×` 는 같은 칸이 아니라
         **형제 칸**이라 덮이지 않는다 — 그 구조와 동작을 함께 건다. */
      await connect(true, opened, [memoSide, aiSide]);
      fireEvent.click(screen.getByRole("tab", { name: "메모" }));
      const drop = screen.getByRole("button", { name: meetingScreen.memoLineDrop });
      // 넓힌 글자 «안» 에 들어가 있지 않다
      expect(memoSlot().contains(drop)).toBe(false);

      vi.mocked(api.removeMeetingMemoLine).mockResolvedValue(undefined);
      fireEvent.click(drop);
      await waitFor(() => expect(api.removeMeetingMemoLine).toHaveBeenCalledWith("m1", "m-1", "ml1"));
    });

    it("과녁을 넓혀도 **편집 전후로 박스가 안 생긴다** — 계약은 그대로다", async () => {
      await connect(true, opened, [memoSide, aiSide]);
      fireEvent.click(screen.getByRole("tab", { name: "메모" }));
      const slot = memoSlot();
      const closed = slot.className;
      fireEvent.click(slot);
      // 같은 노드이고 클래스도 그대로다 — 모양을 바꿀 고리가 없다
      expect(memoSlot()).toBe(slot);
      expect(slot.className).toBe(closed);
      for (const shell of ["scax-textfield", "scax-field", "scax-textarea", "meeting-line-edit"]) {
        expect(slot.classList.contains(shell)).toBe(false);
      }
      const body = document.querySelector(".scax-note__body") as HTMLElement;
      expect(body.querySelector("input")).toBeNull();
      expect(body.querySelector("textarea")).toBeNull();
    });

    it("**AI 벌 줄에는 안 단다** — 고치는 자리도 빼는 자리도 없다", async () => {
      await connect(true, opened, [memoSide, aiSide]);
      fireEvent.click(screen.getByRole("tab", { name: "AI 요약" }));
      expect(screen.queryByLabelText(meetingScreen.memoLineEdit)).toBeNull();
      expect(screen.queryByRole("button", { name: meetingScreen.memoLineDrop })).toBeNull();
      expect(screen.getByText("AI 가 적은 줄.")).toBeTruthy();
    });

    it("`can_edit_agendas.memo` 가 거짓이면 줄도 안 열린다 — 안건 게이트와 **같은 값**이다", async () => {
      await connect(true, { can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: true, ai: false, final: false } }, [memoSide, aiSide]);
      fireEvent.click(screen.getByRole("tab", { name: "메모" }));
      expect(screen.queryByLabelText(meetingScreen.memoLineEdit)).toBeNull();
      expect(screen.queryByRole("button", { name: meetingScreen.memoLineDrop })).toBeNull();
      // 줄은 «글자로» 그대로 선다
      expect(screen.getByText("사람이 적은 줄.")).toBeTruthy();
    });
  });

  /* ──────────────────────────────────────────────────────────────────────────
     **진행 중 표시는 그 일을 시킨 자리에만 선다** (2026-09-15 버그).
     깃발 하나가 화면 전체를 잠가서, 메모를 저장하는 동안 [회의 종료]가 깜박였다.
     이제 키가 조작마다 하나고, 막는 것은 ① 같은 조작 두 번 ② 생애주기 셋끼리 뿐이다.
     ────────────────────────────────────────────────────────────────────────── */
  it("자리표시 제목은 번호만 낸다 — 「안건 1. 안건 1」로 두 번 붙지 않는다 (§12 R-50)", async () => {
    /* 서버가 빈 제목 + `title_placeholder: true` 로 낸다. 예전에는 제목 자리에 「안건 1」이
       들어와 라벨의 번호와 겹쳤다. 없는 제목을 지어내지 않고 번호만 낸다. */
    const placeholder: MeetingAgenda = { ...memoSide, title: "", title_placeholder: true, lines: [] };
    await connect(true, {}, [placeholder]);

    fireEvent.click(screen.getByRole("tab", { name: "메모" }));
    /* 회의록 칸 «안» 에서만 본다 — 메모 칸의 대상 드롭다운도 「안건 1」이라 문서 전체로 보면 둘이다 */
    const body = within(document.querySelector(".scax-note__body") as HTMLElement);
    expect(body.getByText("안건 1")).toBeTruthy();
    expect(body.queryByText(/안건 1\. /)).toBeNull();
  });
});
