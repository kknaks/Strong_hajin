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

const agenda: MeetingAgenda = {
  agenda_id: "a1",
  last_saved_at: "2026-09-08T07:00:00Z",
  order: 1,
  title: "토큰 수요 전망",
  source: "manual",
  concluded: false,
  lines: [{ line_id: "l1", track: "ai", order: 1, text: "회의 전에 AI 가 적어 둔 줄.", author: "AI", at_ms: null, evidence: [] }],
  todos: [],
};

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
    can_edit_agendas: false,
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
async function watch(over: Partial<MeetingInfo> = {}, agendas: MeetingAgenda[] = [agenda]) {
  const rendered = renderLive(false, over, agendas);
  await screen.findByText("DB ax 전략");
  return rendered;
}

/** `ready` 까지 밟아 연결을 세운다. */
async function connect(canWriteMemo = true, over: Partial<MeetingInfo> = {}, agendas: MeetingAgenda[] = [agenda]) {
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
    fireEvent.click(screen.getByRole("button", { name: "스크립트" }));
    expect(screen.getByText("받아 적힌 말.")).toBeTruthy();

    // 누가 남긴 메모 — 보는 창도 같은 프레임으로 받는다
    socket().emit({
      type: "memo.line",
      agendaId: "a1",
      line: { line_id: "m1", track: "memo", order: 1, text: "다른 사람이 남긴 메모.", author: "1", at_ms: 70_000, evidence: [] },
    });
    fireEvent.click(screen.getByRole("tab", { name: "메모" }));
    expect(screen.getByText("다른 사람이 남긴 메모.")).toBeTruthy();

    // AI 요약
    socket().emit({
      type: "ai.batch",
      seq: 1,
      agendas: [{ ...agenda, lines: [{ line_id: "b1", track: "ai", order: 1, text: "배치가 쓴 줄.", author: null, at_ms: null, evidence: [] }] }],
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
    fireEvent.click(screen.getByRole("button", { name: "스크립트" }));
    expect(screen.getByText("계속 받습니다.")).toBeTruthy();
  });

  it("잠정 발화는 교체되고 확정 발화는 쌓인다", async () => {
    await connect(true);
    fireEvent.click(screen.getByRole("button", { name: "스크립트" }));

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
      evidence: [],
    });
    fireEvent.change(screen.getByLabelText("메모를 남기세요"), { target: { value: "그 안건에 붙는 메모." } });
    fireEvent.click(screen.getByRole("button", { name: "메모 남기기" }));
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
    fireEvent.click(screen.getByRole("button", { name: "스크립트" }));

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

    const pane = document.querySelector(".meeting-scroll") as HTMLElement;
    Object.defineProperty(pane, "scrollHeight", { configurable: true, value: 900 });
    Object.defineProperty(pane, "clientHeight", { configurable: true, value: 300 });
    pane.scrollTop = 120;

    socket().emit({
      type: "ai.batch",
      seq: 1,
      agendas: [
        {
          ...agenda,
          agenda_id: "batch-1",
          title: "토큰 수요 전망",
          lines: [{ line_id: "b1", track: "ai", order: 1, text: "배치가 새로 쓴 줄.", author: "AI", at_ms: null, evidence: [] }],
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
    expect(screen.queryByRole("button", { name: "메모 남기기" })).toBeNull();
    cleanup();

    await connect(true);
    expect(screen.getByLabelText("메모를 남기세요")).toBeTruthy();
  });

  it("메모는 돌아온 줄만 붙는다 — 실패하면 친 것을 그대로 두고 알린다", async () => {
    await connect(true);
    vi.mocked(api.addMeetingMemoLine).mockRejectedValueOnce(new Error("boom"));
    fireEvent.change(screen.getByLabelText("메모를 남기세요"), { target: { value: "분기별로 다시 뽑기로." } });
    fireEvent.click(screen.getByRole("button", { name: "메모 남기기" }));
    expect(await screen.findByText("저장하지 못했습니다. 다시 시도하고 있습니다.")).toBeTruthy();
    expect((screen.getByLabelText("메모를 남기세요") as HTMLInputElement).value).toBe("분기별로 다시 뽑기로.");

    vi.mocked(api.addMeetingMemoLine).mockResolvedValueOnce({
      line_id: "m1",
      track: "memo",
      order: 1,
      text: "분기별로 다시 뽑기로.",
      author: "1",
      at_ms: 120_000,
      evidence: [],
    });
    fireEvent.click(screen.getByRole("button", { name: "메모 남기기" }));
    await waitFor(() => expect(api.addMeetingMemoLine).toHaveBeenCalledWith("m1", "a1", "분기별로 다시 뽑기로."));
    expect(await screen.findByText("분기별로 다시 뽑기로.")).toBeTruthy();
    // 스크립트에는 서지 않는다 (사용자 결정) — 전사만 그 자리에 선다
    fireEvent.click(screen.getByRole("button", { name: "스크립트" }));
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
      evidence: [],
    });
    fireEvent.change(screen.getByLabelText("메모를 남기세요"), { target: { value: "작성자 없이 온 줄." } });
    fireEvent.click(screen.getByRole("button", { name: "메모 남기기" }));
    expect(await screen.findByText("작성자 없이 온 줄.")).toBeTruthy();

    // 스크립트는 전사만 낸다 — 확정 발화가 오면 그것만 선다
    socket().emit({ type: "transcript.final", item: { id: "f1", speakerLabel: "화자 1", atMs: 61_000, endMs: 64_000, content: "전사만 선다." } });
    fireEvent.click(screen.getByRole("button", { name: "스크립트" }));
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
    fireEvent.click(screen.getByRole("button", { name: "스크립트" }));
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
    fireEvent.click(screen.getByRole("button", { name: "스크립트" }));
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
    fireEvent.click(screen.getByRole("button", { name: "스크립트" }));
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
      ...agenda,
      lines: [
        {
          line_id: "l7",
          track: "ai",
          order: 1,
          text: "AI 가 회의 중에 적은 줄.",
          author: null,
          at_ms: null,
          evidence: [{ start_ms: 120_000, end_ms: 150_000 }],
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
          ...agenda,
          lines: [{ line_id: "b1", track: "ai", order: 1, text: "배치가 쓴 줄.", author: null, at_ms: null, evidence: [] }],
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
      agendas: [{ ...agenda, todos: [todo({ todo_id: "p1", title: "1회차가 낸 후보" })] }],
    });
    expect(screen.getByText("1회차가 낸 후보")).toBeTruthy();

    socket().emit({
      type: "ai.batch",
      seq: 2,
      agendas: [{ ...agenda, todos: [todo({ todo_id: "p2", title: "2회차가 낸 후보" })] }],
    });
    // 줄 id 를 붙들지 않는다 — 이번 회차가 낸 것이 곧 전부다
    expect(screen.getByText("2회차가 낸 후보")).toBeTruthy();
    expect(screen.queryByText("1회차가 낸 후보")).toBeNull();
  });

  it("배치 전에 들어온 참여자는 상세의 잠정 후보로 선다 — 메모 탭에는 없다 (D46)", async () => {
    const loaded: MeetingAgenda = {
      ...agenda,
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
