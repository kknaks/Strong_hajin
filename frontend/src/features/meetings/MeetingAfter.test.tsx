import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async (actual) => ({
  // ApiError 는 진짜를 쓴다 — 화면이 409 를 `instanceof` 로 가른다
  ApiError: ((await actual()) as { ApiError: unknown }).ApiError,
  readMeeting: vi.fn(),
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

import { ApiError } from "../../lib/api";
import * as api from "../../lib/api";
import type { MeetingAgenda, MeetingInfo, MeetingRecord } from "../../lib/viewModels";
import { useState } from "react";

import { MeetingDetailPage } from "./MeetingDetailPage";
import { resetRoster } from "./roster";

const agenda: MeetingAgenda = {
  agenda_id: "a1",
  last_saved_at: "2026-09-08T07:00:00Z",
  order: 1,
  title: "토큰 수요 전망",
  source: "manual",
  concluded: true,
  lines: [
    {
      line_id: "l1",
      track: "final",
      order: 1,
      text: "수요는 구조적으로 는다는 전제에 합의했다.",
      author: null,
      at_ms: null,
      evidence: [{ start_ms: 120_000, end_ms: 150_000 }],
    },
  ],
  todos: [
    {
      provisional: false,
      todo_id: "t1",
      agenda_id: "a1",
      title: "전망치 다시 뽑기",
      description: "분기별 전망치를 다시 뽑는다.",
      due_candidate: "2026-09-12",
      checklist_candidate: ["지난 분기 실적 모으기"],
      reference: { meeting_id: "m1", agenda_id: "a1", line_ids: ["l1"] },
      linked: null,
    },
  ],
};
/* 바퀴 6a: 첨부·스크립트 칸은 셸의 «4칸» 에 포털로 앉는다(M-5). 화면만 떼어 렌더하면 그 자리가
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


function meeting(over: Partial<MeetingInfo> = {}): MeetingInfo {
  return {
    meeting_id: "m1",
    title: "DB ax 전략",
    purpose: null,
    starts_at: "2026-09-08T06:30:00Z",
    ends_at: "2026-09-08T07:00:00Z",
    location: "대회의실",
    status: "done",
    created_by: "1",
    attendees: [
      { member_id: "1", display_name: "이건학" },
      { member_id: "2", display_name: "정우성" },
    ],
    external_attendees: [],
    viewer_relation: "attendee",
    can_edit_info: true,
    can_edit_note: true,
    can_edit_agendas: true,
    can_write_memo: false,
    last_saved_at: "2026-09-08T07:08:00Z",
    started_at: "2026-09-08T06:30:00Z",
    carried_from_meeting_id: null,
    title_candidate: null,
    failure_reason: null,
    room_reservation: null,
    ...over,
  };
}

function renderAfter(over: Partial<MeetingInfo> = {}, agendas: MeetingAgenda[] = [agenda]) {
  const value: MeetingRecord = { meeting: meeting(over), agendas };
  vi.mocked(api.readMeeting).mockResolvedValue(value);
  const onNotice = vi.fn();
  render(
    <DetailHost
      canCreateWorkRequests
      meetingId="m1"
      onBack={vi.fn()}
      onError={vi.fn()}
      onNotice={onNotice}
      onOpenMeeting={vi.fn()}
      onSessionLost={vi.fn()}
      ownerName="이건학"
    />,
  );
  return { onNotice };
}

beforeEach(() => {
  resetRoster();
  vi.mocked(api.readMeetingRooms).mockResolvedValue([]);
  vi.mocked(api.getOrganizationTree).mockResolvedValue([]);
  vi.mocked(api.getOrganizationUnitMembers).mockResolvedValue([]);
  vi.mocked(api.readMeetingMaterials).mockResolvedValue([]);
  vi.mocked(api.readMeetingShares).mockResolvedValue([]);
  vi.mocked(api.getTasks).mockResolvedValue([]);
  vi.mocked(api.getWorkRequestCcCandidates).mockResolvedValue([]);
  vi.mocked(api.getWorkRequestAssigneeCandidates).mockResolvedValue([
    { id: "9", display_name: "한서린" },
    { id: "2", display_name: "정우성" },
  ]);
  vi.mocked(api.readMeetingTranscript).mockResolvedValue({
    items: [
      { id: "b1", speakerLabel: "1", atMs: 60_000, endMs: 90_000, content: "먼저 전제부터 맞춰 봅시다." },
      { id: "b2", speakerLabel: "2", atMs: 130_000, endMs: 160_000, content: "수요는 계단식으로 올라갑니다." },
    ],
    memos: [{ line_id: "m1", agenda_id: "a1", text: "분기별로 다시 뽑기로.", author: "1", atMs: 100_000 }],
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SCR-106 회의 뒤 — 실계약 배선", () => {
  it("끝난 회의의 스크립트는 전사만 낸다 — 메모는 섞이지 않는다", async () => {
    renderAfter();
    await screen.findByText("DB ax 전략");
    fireEvent.click(screen.getByRole("button", { name: "스크립트" }));
    const script = await screen.findByRole("list", { name: "스크립트" });

    expect(within(script).getByText("먼저 전제부터 맞춰 봅시다.")).toBeTruthy();
    expect(within(script).getByText("수요는 계단식으로 올라갑니다.")).toBeTruthy();
    // 메모는 이 탭에 서지 않는다 (사용자 결정) — 같은 말을 두 자리에 두지 않는다
    expect(within(script).queryByText("분기별로 다시 뽑기로.")).toBeNull();
    expect(within(script).queryByText("메모")).toBeNull();
    expect(within(script).getAllByRole("listitem")).toHaveLength(2);
    // 전사가 주는 것은 번호뿐이다 — 화면이 「화자 N」으로 읽는다 (C7)
    expect(within(script).getByText("화자 1")).toBeTruthy();
    expect(within(script).getByText("화자 2")).toBeTruthy();
    expect(within(script).queryByText("1")).toBeNull();
    // 눈금은 회의 경과다 (D50) — 시작에서 1분
    expect(within(script).getByText("01:00")).toBeTruthy();

    // 한 줄에 세 칸이다 — 시각 · 화자 · 내용
    const first = within(script).getAllByRole("listitem")[0];
    expect(first.querySelector(".gutter-meta")?.textContent).toBe("01:00");
    expect(first.querySelector(".gutter-aside")?.textContent).toBe("화자 1");
    expect(first.querySelector(".gutter-body")?.textContent).toBe("먼저 전제부터 맞춰 봅시다.");
  });

  it("안건 출처는 다섯이고, 모르는 값이면 그 자리가 서지 않는다", async () => {
    renderAfter({}, [
      { ...agenda, agenda_id: "s1", order: 1, title: "직접 쓴 것", source: "manual" },
      { ...agenda, agenda_id: "s2", order: 2, title: "세트에서", source: "set" },
      { ...agenda, agenda_id: "s3", order: 3, title: "지난 회의", source: "carried" },
      { ...agenda, agenda_id: "s4", order: 4, title: "다른 회의", source: "derived" },
      { ...agenda, agenda_id: "s5", order: 5, title: "AI 가 세운 것", source: "ai" },
      // 계약에 없는 값이 와도 화면이 깨지지 않는다 — 그 자리가 그냥 서지 않을 뿐이다
      { ...agenda, agenda_id: "s6", order: 6, title: "모르는 출처", source: "sideways" as MeetingAgenda["source"] },
    ]);
    await screen.findByText("DB ax 전략");
    // 완료된 회의에서도 출처는 사라지지 않는다 (E21 「상태와 무관하게 늘 낸다」)
    for (const label of ["직접 입력", "세트", "지난 회의에서 넘어옴", "다른 회의에서 파생", "AI 정리"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.queryByText("sideways")).toBeNull();
    expect(screen.getByText("안건 6. 모르는 출처")).toBeTruthy();
  });

  it("안건 번호는 목록에서의 자리다 — order 값을 그대로 쓰지 않는다", async () => {
    // 바로 시작한 회의의 기본 안건은 order 0 에서 시작한다. 그래도 화면은 「안건 1.」부터다
    renderAfter({}, [
      { ...agenda, agenda_id: "a2", order: 1, title: "온보딩 자료", lines: [], todos: [] },
      { ...agenda, agenda_id: "a1", order: 0, title: "안건 하나", lines: [], todos: [] },
    ]);
    await screen.findByText("DB ax 전략");
    // order 로 줄만 세우고 번호는 그 줄에서의 자리다
    expect(screen.getByText("안건 1. 안건 하나")).toBeTruthy();
    expect(screen.getByText("안건 2. 온보딩 자료")).toBeTruthy();
    expect(screen.queryByText(/^안건 0\./)).toBeNull();
  });

  it("근거 칩은 스크립트 탭으로 옮기고 그 구간만 켠다", async () => {
    renderAfter();
    await screen.findByText("DB ax 전략");
    const chip = screen.getByRole("button", { name: "02:00" });
    fireEvent.click(chip);

    const script = await screen.findByRole("list", { name: "스크립트" });
    const rows = within(script).getAllByRole("listitem");
    // 120_000~150_000 안에 있는 줄만 켜진다 — 01:00 발화도 01:40 메모도 아니다
    expect(rows.filter((row) => row.className.includes("active"))).toHaveLength(1);
    expect(rows.find((row) => row.className.includes("active"))?.textContent).toContain("수요는 계단식으로 올라갑니다.");
  });

  it("근거 구간이 발화 한가운데서 시작해도 그 줄이 켜진다 — 겹치면 켠다 (D50)", async () => {
    const midway: MeetingAgenda = {
      ...agenda,
      // 전사 둘째 줄은 130_000~160_000 이고, 근거는 그 «안» 에서 시작해 그 안에서 끝난다.
      // 줄의 시작 시각만 보던 때는 이런 구간에서 아무 줄도 켜지지 않았다(실측 8구간 중 5).
      lines: [{ ...agenda.lines[0], evidence: [{ start_ms: 140_000, end_ms: 145_000 }] }],
    };
    renderAfter({}, [midway]);
    await screen.findByText("DB ax 전략");

    fireEvent.click(screen.getByRole("button", { name: "02:20" }));
    const script = await screen.findByRole("list", { name: "스크립트" });
    const rows = within(script).getAllByRole("listitem");
    expect(rows.filter((row) => row.className.includes("active"))).toHaveLength(1);
    expect(rows.find((row) => row.className.includes("active"))?.textContent).toContain("수요는 계단식으로 올라갑니다.");
  });

  it("근거 구간이 여럿이면 칩도 전부 선다 — 시작이 같은 것은 하나로 접고 시각 순이다 (D49)", async () => {
    const many: MeetingAgenda = {
      ...agenda,
      lines: [
        {
          ...agenda.lines[0],
          evidence: [
            { start_ms: 120_000, end_ms: 150_000 },
            { start_ms: 60_000, end_ms: 70_000 },
            // 시작이 같은 구간 — 하나로 접히고 넓은 쪽(끝이 늦은 쪽)이 남는다
            { start_ms: 60_000, end_ms: 95_000 },
          ],
        },
      ],
    };
    renderAfter({}, [many]);
    await screen.findByText("DB ax 전략");

    const line = screen.getByText("수요는 구조적으로 는다는 전제에 합의했다.").closest("li") as HTMLElement;
    const chips = within(line).getAllByRole("button");
    expect(chips.map((chip) => chip.textContent)).toEqual(["01:00", "02:00"]);

    // 칩마다 «자기» 구간으로 간다 — 첫 칩은 60_000~95_000 안의 발화를 켠다
    fireEvent.click(chips[0]);
    const script = await screen.findByRole("list", { name: "스크립트" });
    const active = () => within(script).getAllByRole("listitem").filter((row) => row.className.includes("active"));
    expect(active()).toHaveLength(1);
    expect(active()[0].textContent).toContain("먼저 전제부터 맞춰 봅시다.");

    fireEvent.click(chips[1]);
    expect(active()).toHaveLength(1);
    expect(active()[0].textContent).toContain("수요는 계단식으로 올라갑니다.");
  });

  it("[업무 생성]은 담당을 비운 채 열고 promote 로 보낸다 — 회의록에 업무 링크를 두지 않는다", async () => {
    const { onNotice } = renderAfter();
    await screen.findByText("전망치 다시 뽑기");
    fireEvent.click(screen.getByRole("button", { name: "업무 생성" }));
    const drawer = await screen.findByRole("dialog", { name: "업무 요청" });

    // 담당은 비어 있고 참석자(정우성)가 후보 목록 앞에 선다
    // 후보 목록은 드로어가 뜬 «뒤» 에 도착한다 — 기다리지 않으면 빈 목록을 읽는다
    const assignee = within(drawer).getByRole("button", { name: "담당 후보" });
    await waitFor(() => expect(assignee.textContent).toContain("선택"));
    fireEvent.click(assignee);
    expect(screen.getAllByRole("option").map((one) => one.textContent)[0]).toContain("정우성");
    fireEvent.click(screen.getAllByRole("option")[0]);

    vi.mocked(api.promoteMeetingTodo).mockResolvedValue({ ...agenda.todos[0], linked: { work_request_id: "wr1", task_id: null } });
    vi.mocked(api.readMeeting).mockResolvedValue({
      meeting: meeting(),
      agendas: [{ ...agenda, todos: [{ ...agenda.todos[0], linked: { work_request_id: "wr1", task_id: null } }] }],
    });
    fireEvent.click(within(drawer).getByRole("button", { name: "업무 요청 보내기" }));

    await waitFor(() =>
      expect(api.promoteMeetingTodo).toHaveBeenCalledWith("m1", "t1", {
        assignee_id: "2",
        title: "전망치 다시 뽑기",
        description: "분기별 전망치를 다시 뽑는다.",
        due_date: "2026-09-12",
        checklist: ["지난 분기 실적 모으기"],
      }),
    );
    // 업무 요청을 직접 만들지 않는다 — 출처 두 열이 실려야 한다
    expect(api.createWorkRequest).not.toHaveBeenCalled();
    expect(await screen.findByText("요청됨")).toBeTruthy();
    // 회의록에 그 업무로 가는 링크를 두지 않는다
    expect(screen.queryByRole("button", { name: /연관 업무/ })).toBeNull();
    expect(onNotice).toHaveBeenCalled();
  });

  it("이미 보낸 후보를 다시 보내면 지금 있는 것으로 맞춘다", async () => {
    const { onNotice } = renderAfter();
    await screen.findByText("전망치 다시 뽑기");
    fireEvent.click(screen.getByRole("button", { name: "업무 생성" }));
    const drawer = await screen.findByRole("dialog", { name: "업무 요청" });
    const assignee = within(drawer).getByRole("button", { name: "담당 후보" });
    await waitFor(() => expect(assignee.textContent).toContain("선택"));
    fireEvent.click(assignee);
    fireEvent.click(screen.getAllByRole("option")[0]);

    vi.mocked(api.promoteMeetingTodo).mockRejectedValue(new ApiError(409, "already promoted"));
    fireEvent.click(within(drawer).getByRole("button", { name: "업무 요청 보내기" }));
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("이미 업무 요청으로 보낸 후보입니다."));
  });

  it("「실패」의 [다시 시도]는 합성만 다시 건다", async () => {
    renderAfter({ status: "failed", failure_reason: "provider 응답이 스키마를 벗어났습니다" });
    expect(await screen.findByText(/회의 내용은 저장됐지만/)).toBeTruthy();
    expect(screen.getByText("provider 응답이 스키마를 벗어났습니다")).toBeTruthy();

    vi.mocked(api.retryMeetingFinalize).mockResolvedValue(undefined);
    vi.mocked(api.readMeeting).mockResolvedValue({ meeting: meeting({ status: "summarizing" }), agendas: [agenda] });
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    await waitFor(() => expect(api.retryMeetingFinalize).toHaveBeenCalledWith("m1"));
    // 받은 발화와 메모는 건드리지 않는다 — 종료를 다시 부르지 않는다
    expect(api.endMeeting).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByText("정리하는 중")).toBeTruthy());
  });

  it("저장 충돌은 덮어쓰지 않고 지금 있는 줄로 갈아 끼운다", async () => {
    const { onNotice } = renderAfter();
    await screen.findByText("DB ax 전략");
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    fireEvent.change(screen.getAllByLabelText("내용을 한 줄로 적으세요")[0], { target: { value: "내가 고친 줄." } });

    vi.mocked(api.updateMeetingAgenda).mockRejectedValue(
      new ApiError(409, "Conflict", {
        code: "meeting_agenda_stale",
        current: {
          ...agenda,
          last_saved_at: "2026-09-08T07:30:00Z",
          lines: [{ line_id: "l9", track: "final", order: 1, text: "다른 사람이 먼저 쓴 줄.", author: null, at_ms: null, evidence: [] }],
        },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("다른 곳에서 먼저 저장됐습니다. 지금 있는 내용으로 바꿔 두었습니다."));
    expect((await screen.findAllByLabelText("내용을 한 줄로 적으세요"))[0]).toHaveProperty("value", "다른 사람이 먼저 쓴 줄.");
    expect(screen.queryByDisplayValue("내가 고친 줄.")).toBeNull();
  });

  it("제목 후보는 흐리게 서고 연필로 열면 칸에 차 있다", async () => {
    renderAfter({ title: null, title_candidate: "DB AX 전환 범위 논의" });
    expect(await screen.findByText("제목 후보 DB AX 전환 범위 논의")).toBeTruthy();
    expect(screen.getByText("제목 없는 회의")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "회의 정보 수정" }));
    expect((screen.getByLabelText("회의명") as HTMLInputElement).value).toBe("DB AX 전환 범위 논의");
  });

  it("내보내기는 형식 하나짜리 링크다 — 고르는 자리가 없다", async () => {
    renderAfter();
    await screen.findByText("DB ax 전략");
    const link = screen.getByRole("link", { name: "내보내기" });
    expect(link.getAttribute("href")).toBe("/api/meetings/m1/export?format=html");
    expect(screen.queryByText(/pdf|docx|PDF|DOCX/)).toBeNull();
  });

  it("공유받은 사람에게는 조작이 하나도 없다 — 회의록·스크립트·내보내기만", async () => {
    renderAfter({
      viewer_relation: "shared",
      can_edit_info: false,
      can_edit_note: false,
      can_edit_agendas: false,
    });
    await screen.findByText("DB ax 전략");
    expect(screen.getByRole("link", { name: "내보내기" })).toBeTruthy();
    for (const name of ["공유", "수정", "회의 정보 수정", "자료 첨부", "업무 생성", "후보 빼기", "다음 회의 예약"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    expect(screen.queryByRole("button", { name: "자료" })).toBeNull();
    expect(screen.getByRole("button", { name: "스크립트" })).toBeTruthy();
  });

  it("「정리 중」에는 상태를 다시 묻고, 끝나면 묻기를 멈춘다", async () => {
    vi.useFakeTimers();
    try {
      const settling = { meeting: meeting({ status: "summarizing", can_edit_note: false, can_edit_agendas: false }), agendas: [agenda] };
      vi.mocked(api.readMeeting).mockResolvedValue(settling);
      renderAfter({ status: "summarizing", can_edit_note: false, can_edit_agendas: false });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(screen.getByText("정리하는 중")).toBeTruthy();
      const firstReads = vi.mocked(api.readMeeting).mock.calls.length;

      // 합성이 끝났다고 알려 줄 연결은 그때 이미 닫혀 있다 — 화면이 다시 물어서 안다
      vi.mocked(api.readMeeting).mockResolvedValue({ meeting: meeting(), agendas: [agenda] });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
      expect(vi.mocked(api.readMeeting).mock.calls.length).toBeGreaterThan(firstReads);
      expect(screen.queryByText("정리하는 중")).toBeNull();
      expect(screen.getByText("수요는 구조적으로 는다는 전제에 합의했다.")).toBeTruthy();

      // 「완료」가 된 뒤에는 더 묻지 않는다
      const settledReads = vi.mocked(api.readMeeting).mock.calls.length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20_000);
      });
      expect(vi.mocked(api.readMeeting).mock.calls.length).toBe(settledReads);
    } finally {
      vi.useRealTimers();
    }
  });

  it("[공유]는 끝난 뒤에만 선다 — 예정·진행 중·정리 중에는 없다", async () => {
    for (const status of ["scheduled", "in_progress", "summarizing"] as const) {
      renderAfter({ status });
      await screen.findByText("DB ax 전략");
      expect(screen.queryByRole("button", { name: "공유" })).toBeNull();
      cleanup();
    }
    for (const status of ["done", "failed"] as const) {
      renderAfter({ status });
      await screen.findByText("DB ax 전략");
      expect(screen.getByRole("button", { name: "공유" })).toBeTruthy();
      cleanup();
    }
  });

  it("상태 어휘는 다섯이고 「정리 중」에는 배지가 없다", async () => {
    renderAfter({ status: "cancelled" });
    await screen.findByText("DB ax 전략");
    // 「취소됨」이 아니라 「취소」다 (D34)
    expect(document.querySelector(".scax-badge")?.textContent).toBe("취소");
    cleanup();

    renderAfter({ status: "summarizing" });
    await screen.findByText("DB ax 전략");
    // 배지를 두지 않는다 — 회의록 자리의 로딩과 「정리하는 중」 한 줄이 이미 말한다
    expect(document.querySelector(".scax-badge")).toBeNull();
    expect(screen.getByText("정리하는 중")).toBeTruthy();
  });

  it("「정리 중」은 배지 없이 로딩과 한 줄만 내고 스크립트는 그대로 읽는다", async () => {
    renderAfter({ status: "summarizing", can_edit_note: false, can_edit_agendas: false });
    await screen.findByText("DB ax 전략");
    expect(screen.getByText("정리하는 중")).toBeTruthy();
    // 영역 로딩 — 「정리 중」은 회의록 자리를 스켈레톤으로 잡아 둔다 (M2)
    expect(screen.getAllByRole("status").some((one) => one.className.includes("skeleton"))).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "스크립트" }));
    expect(await within(await screen.findByRole("list", { name: "스크립트" })).findByText("먼저 전제부터 맞춰 봅시다.")).toBeTruthy();
  });
});
