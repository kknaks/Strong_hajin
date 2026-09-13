import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async (actual) => ({
  // ApiError 는 진짜를 쓴다 — 화면이 409 를 `instanceof` 로 가른다
  ApiError: ((await actual()) as { ApiError: unknown }).ApiError,
  readMeetingTranscript: vi.fn(),
  readMeetingRooms: vi.fn(),
  readMeetingMaterials: vi.fn(),
  readMeetingMaterialText: vi.fn(),
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
  bookMeeting: vi.fn(),
  getOrganizationTree: vi.fn(),
  getOrganizationUnitMembers: vi.fn(),
  // [업무 생성]이 여는 현행 업무 요청 모달(WorkModals)이 쓰는 것들
  getWorkRequestAssigneeCandidates: vi.fn(),
  getWorkRequestCcCandidates: vi.fn(),
  createWorkRequest: vi.fn(),
  createDirectTask: vi.fn(),
  assignTask: vi.fn(),
  getTasks: vi.fn(),
}));

import * as api from "../../lib/api";
import type { MeetingAgenda, MeetingInfo, MeetingRecord, MeetingStatus } from "../../lib/viewModels";
import { useState } from "react";

import { MeetingDetailPage } from "./MeetingDetailPage";
import { resetRoster } from "./roster";

const agenda: MeetingAgenda = {
  agenda_id: "a1",
  last_saved_at: "2026-09-08T07:00:00Z",
  order: 1,
  title: "토큰 수요 전망",
  source: "carried",
  concluded: true,
  lines: [
    { line_id: "l1", track: "final", order: 1, text: "수요는 구조적으로 는다는 전제에 합의했다.", author: "AI", at_ms: null, evidence: [{ start_ms: 120_000, end_ms: 150_000 }] },
    { line_id: "l2", track: "memo", order: 1, text: "분기별로 다시 뽑기로.", author: "이건학", at_ms: null, evidence: [] },
    // AI 가 낸 줄과 합쳐진 최종 줄에는 작성자가 없다 — 서버가 null 로 낸다 (W9)
    { line_id: "l3", track: "ai", order: 1, text: "대응은 확장과 효율 두 축이다.", author: null, at_ms: null, evidence: [] },
  ],
  todos: [
    {
      provisional: false,
      todo_id: "t1",
      agenda_id: "a1",
      title: "전망치 다시 뽑기",
      description: "분기별 전망치를 다시 뽑아 다음 회의에 올린다.",
      due_candidate: "2026-09-12",
      checklist_candidate: ["지난 분기 실적 모으기", "전망치 초안 쓰기"],
      reference: { meeting_id: "m1", agenda_id: "a1", line_ids: [] },
      linked: null,
    },
  ],
};

function meeting(over: Partial<MeetingInfo> = {}): MeetingInfo {
  return {
    meeting_id: "m1",
    title: "DB ax 전략",
    purpose: "AX 전환 범위를 정한다.",
    starts_at: "2026-09-08T06:30:00Z",
    ends_at: "2026-09-08T07:00:00Z",
    location: "대회의실",
    status: "done",
    created_by: "이건학",
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
    started_at: "2026-09-08T06:30:00Z",
    title_candidate: null,
    failure_reason: null,
    room_reservation: null,
    last_saved_at: "2026-09-08T07:08:00Z",
    carried_from_meeting_id: null,
    ...over,
  };
}

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

function renderDetail(over: Partial<MeetingInfo> = {}, agendas: MeetingAgenda[] = [agenda]) {
  const value: MeetingRecord = { meeting: meeting(over), agendas };
  vi.mocked(api.readMeeting).mockResolvedValue(value);
  const onNotice = vi.fn();
  render(
    <DetailHost
      meetingId="m1"
      onBack={vi.fn()}
      onError={vi.fn()}
      onNotice={onNotice}
      canCreateWorkRequests
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
  vi.mocked(api.readMeetingTranscript).mockResolvedValue({ items: [], memos: [] });
  vi.mocked(api.getTasks).mockResolvedValue([]);
  vi.mocked(api.getWorkRequestCcCandidates).mockResolvedValue([]);
  vi.mocked(api.getWorkRequestAssigneeCandidates).mockResolvedValue([
    { id: "9", display_name: "한서린" },
    { id: "2", display_name: "정우성" },
  ]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const pencil = () => screen.queryByRole("button", { name: "회의 정보 수정" });

describe("SCR-106 회의 상세 — 상태와 관계가 무엇을 낼지 정한다", () => {
  it("연필은 「예정」·「완료」에만 선다", async () => {
    for (const status of ["scheduled", "done"] as MeetingStatus[]) {
      renderDetail({ status });
      await screen.findByText("DB ax 전략");
      expect(pencil()).toBeTruthy();
      cleanup();
    }
  });

  it("진행 중 · 정리 중 · 실패 · 취소됨에는 연필이 서지 않는다", async () => {
    for (const status of ["in_progress", "summarizing", "failed", "cancelled"] as MeetingStatus[]) {
      renderDetail({ status });
      await screen.findByText("DB ax 전략");
      expect(pencil()).toBeNull();
      cleanup();
    }
  });

  it("만든 사람이 아닌 참석자에게도 연필은 서고, 회의록 [수정]은 서지 않는다", async () => {
    renderDetail({ can_edit_info: true, can_edit_note: false, can_edit_agendas: false });
    await screen.findByText("DB ax 전략");
    expect(pencil()).toBeTruthy();
    expect(screen.queryByRole("button", { name: "수정" })).toBeNull();
  });

  it("예약값을 고치는 동안 [회의 시작]은 비활성이다", async () => {
    renderDetail({ status: "scheduled" });
    await screen.findByText("DB ax 전략");
    expect(screen.getByRole("button", { name: /회의 시작/ }).hasAttribute("disabled")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "회의 정보 수정" }));
    expect(screen.getByRole("button", { name: /회의 시작/ }).hasAttribute("disabled")).toBe(true);
    // 일시는 30분 눈금이고 장소는 글자다 — 회의실 판정은 이 자리로 오지 않는다
    expect(within(screen.getByLabelText("시작 시각")).getAllByText("15:30")).toHaveLength(1);
    expect(screen.getByLabelText("장소")).toBeTruthy();
  });

  it("머리 편집은 바뀐 것이 없으면 저장할 수 없고, 저장하면 계약대로 보낸다", async () => {
    renderDetail({ status: "scheduled" });
    await screen.findByText("DB ax 전략");
    fireEvent.click(screen.getByRole("button", { name: "회의 정보 수정" }));
    expect(screen.getByRole("button", { name: "저장" }).hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByLabelText("회의명"), { target: { value: "DB ax 전략 2" } });
    vi.mocked(api.updateMeetingInfo).mockResolvedValue({ meeting: meeting({ status: "scheduled" }), agendas: [agenda] });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() =>
      expect(api.updateMeetingInfo).toHaveBeenCalledWith("m1", {
        title: "DB ax 전략 2",
        starts_at: "2026-09-08T15:30:00+09:00",
        ends_at: "2026-09-08T16:00:00+09:00",
        location: "대회의실",
        attendee_ids: ["1", "2"],
      }),
    );
  });

  it("「예정」에서도 [수정]이 서고 안건을 더하고 뺀다 — 줄 편집 칸은 서지 않는다", async () => {
    // BE 가 갈라 내는 두 필드: 「예정」은 안건만 열리고 회의록 줄은 닫혀 있다
    renderDetail({ status: "scheduled", can_edit_note: false, can_edit_agendas: true });
    await screen.findByText("DB ax 전략");
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    expect(screen.queryByLabelText("내용을 한 줄로 적으세요")).toBeNull();
    expect(screen.getByRole("button", { name: "안건 빼기" })).toBeTruthy();

    vi.mocked(api.addMeetingAgenda).mockResolvedValue({ ...agenda, agenda_id: "a2", order: 2, title: "협력 범위" });
    fireEvent.change(screen.getByLabelText("안건을 적으세요"), { target: { value: "협력 범위" } });
    fireEvent.click(screen.getByRole("button", { name: "안건 추가" }));
    await waitFor(() => expect(api.addMeetingAgenda).toHaveBeenCalledWith("m1", "협력 범위"));

    vi.mocked(api.removeMeetingAgenda).mockResolvedValue(undefined);
    fireEvent.click(screen.getByRole("button", { name: "안건 빼기" }));
    fireEvent.click(screen.getByRole("button", { name: "삭제" }));
    await waitFor(() => expect(api.removeMeetingAgenda).toHaveBeenCalledWith("m1", "a1"));
  });

  it("고칠 권한이 둘 다 없으면 [수정] 자체가 서지 않는다", async () => {
    renderDetail({ can_edit_note: false, can_edit_agendas: false });
    await screen.findByText("DB ax 전략");
    expect(screen.queryByRole("button", { name: "수정" })).toBeNull();
  });

  it("작성자가 없는 줄(AI · 최종)이 섞여도 화면이 터지지 않는다", async () => {
    renderDetail({}, [
      {
        ...agenda,
        lines: [
          { line_id: "n1", track: "final", order: 1, text: "AI 가 합친 줄.", author: null, at_ms: null, evidence: [] },
          { line_id: "n2", track: "memo", order: 2, text: "사람이 적은 줄.", author: "이건학", at_ms: null, evidence: [] },
        ],
      },
    ]);
    expect(await screen.findByText("AI 가 합친 줄.")).toBeTruthy();
    // 이름을 지어내지 않는다 — 작성자 자리는 그냥 비어 있다
    expect(screen.queryByText("null")).toBeNull();
    expect(screen.queryByText("undefined")).toBeNull();
  });

  it("본문은 줄 목록이고 줄에 종류 배지가 없다", async () => {
    renderDetail();
    expect(await screen.findByText("수요는 구조적으로 는다는 전제에 합의했다.")).toBeTruthy();
    expect(screen.queryByText("논의")).toBeNull();
    expect(screen.queryByText("결정")).toBeNull();
    // 근거는 줄 «오른쪽» 에 시각 하나다 (D34) — 「근거」 라벨과 칩 나열을 두지 않는다
    expect(screen.queryByText("근거")).toBeNull();
    // 눈금은 회의 경과다 (D50) — 시작에서 2분 뒤에 시작하는 구간이면 02:00
    expect(screen.getByRole("button", { name: "02:00" })).toBeTruthy();
  });

  it("근거 값이 숫자가 아니면 그 시각을 그리지 않는다", async () => {
    renderDetail({}, [
      {
        ...agenda,
        lines: [
          {
            ...agenda.lines[0],
            // 계약 키만 읽는다. 값이 숫자가 아니면 가리킬 수 없는 칩이라 세우지 않는다 (NaN:NaN 금지)
            evidence: [{ start_ms: Number.NaN, end_ms: Number.NaN }, { start_ms: 120_000, end_ms: 150_000 }],
          },
        ],
      },
    ]);
    await screen.findByText("DB ax 전략");
    expect(screen.getByRole("button", { name: "02:00" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /NaN/ })).toBeNull();
    expect(screen.getAllByRole("button", { name: /^\d{2}:\d{2}$/ })).toHaveLength(1);
  });

  it("[수정]은 줄 단위 편집을 열고, [저장]은 빈 줄을 버리고 덮어쓴다", async () => {
    const { onNotice } = renderDetail();
    await screen.findByText("DB ax 전략");
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    const inputs = screen.getAllByLabelText("내용을 한 줄로 적으세요");
    expect(inputs).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: /내용 줄 추가/ }));
    // 안건 계열은 «안건 하나»를 낸다 — 회의 한 벌이 아니다
    vi.mocked(api.updateMeetingAgenda).mockResolvedValue(agenda);
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() =>
      expect(api.updateMeetingAgenda).toHaveBeenCalledWith("m1", "a1", {
        lines: ["수요는 구조적으로 는다는 전제에 합의했다."],
        expected_last_saved_at: "2026-09-08T07:00:00Z",
      }),
    );
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("저장했습니다."));
  });

  it("공유받은 사람에게는 조작 버튼이 하나도 없다 — 회의록과 내보내기만 남는다", async () => {
    renderDetail({ viewer_relation: "shared", can_edit_info: false, can_edit_note: false, can_edit_agendas: false });
    await screen.findByText("DB ax 전략");
    const exportLink = screen.getByRole("link", { name: "내보내기" });
    expect(exportLink.getAttribute("href")).toBe("/api/meetings/m1/export?format=html");
    for (const name of ["공유", "수정", "회의 정보 수정", "자료 첨부", "회의 시작", "업무 생성"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    // 자료 탭도 서지 않는다 — 노출 권한이 참석자다
    expect(screen.queryByRole("button", { name: "자료" })).toBeNull();
    expect(screen.getByRole("button", { name: "스크립트" })).toBeTruthy();
  });

  it("「예정」에는 스크립트 탭이 없고 [공유]도 서지 않는다", async () => {
    renderDetail({ status: "scheduled" });
    await screen.findByText("DB ax 전략");
    expect(screen.queryByRole("button", { name: "스크립트" })).toBeNull();
    expect(screen.queryByRole("button", { name: "공유" })).toBeNull();
  });

  it("자료는 탭 안 미리보기가 아니라 드로어로 열린다", async () => {
    vi.mocked(api.readMeetingMaterials).mockResolvedValue([
      {
        material_id: "mat-1",
        name: "회의 준비 메모.md",
        content_type: "text/markdown",
        size: 12_000,
        uploaded_by: "1",
        uploaded_at: "2026-09-08T06:34:00Z",
        can_detach: true,
      },
    ]);
    vi.mocked(api.readMeetingMaterialText).mockResolvedValue("## 준비 메모\n\n한 줄.");
    renderDetail();
    await screen.findByText("DB ax 전략");
    fireEvent.click(await screen.findByText("회의 준비 메모.md"));
    expect(await screen.findByRole("dialog", { name: "자료 보기" })).toBeTruthy();
    expect(await screen.findByText("준비 메모")).toBeTruthy();
  });

  it("다음 할 일 행에 담당 후보 칸이 없다 — 기한과 [업무 생성]과 후보 빼기만 선다", async () => {
    renderDetail();
    expect(await screen.findByText("전망치 다시 뽑기")).toBeTruthy();
    expect(screen.getByText("2026-09-12")).toBeTruthy();
    expect(screen.getByRole("button", { name: "업무 생성" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "후보 빼기" })).toBeTruthy();
    // 담당은 AI 가 지목하지 않는다 (D19-3)
    expect(screen.queryByText("정우성 · 2026-09-12")).toBeNull();
  });

  it("후보 빼기는 묻지 않고 서버로 간다", async () => {
    renderDetail();
    await screen.findByText("전망치 다시 뽑기");
    vi.mocked(api.removeMeetingTodo).mockResolvedValue(undefined);
    fireEvent.click(screen.getByRole("button", { name: "후보 빼기" }));
    await waitFor(() => expect(api.removeMeetingTodo).toHaveBeenCalledWith("m1", "t1"));
  });

  it("[업무 생성]은 업무 요청 모달을 제목·설명·기한·체크리스트만 채우고 담당은 비운 채로 연다", async () => {
    renderDetail();
    await screen.findByText("전망치 다시 뽑기");
    fireEvent.click(screen.getByRole("button", { name: "업무 생성" }));
    const drawer = await screen.findByRole("dialog", { name: "업무 요청" });
    // 승격은 언제나 업무 요청이다 — 고를 것이 없으니 「업무/요청」 토글을 두지 않는다 (D10)
    expect(within(drawer).queryByRole("tablist", { name: "생성 유형" })).toBeNull();
    expect(within(drawer).queryByRole("tab")).toBeNull();
    expect(within(drawer).getByText("동료가 수락해야 그 사람의 업무가 됩니다. 희망 기한을 함께 보낼 수 있습니다.")).toBeTruthy();
    expect((within(drawer).getByLabelText("요청할 업무") as HTMLInputElement).value).toBe("전망치 다시 뽑기");
    expect(within(drawer).getByDisplayValue("분기별 전망치를 다시 뽑아 다음 회의에 올린다.")).toBeTruthy();
    // 기한 칸은 입력칸이 아니라 달력을 여는 트리거다 (DS-17) — 미리 채운 값은 그 글자로 선다
    expect(within(drawer).getByRole("button", { name: "기한 달력 열기" }).textContent).toBe("2026-09-12");
    expect(within(drawer).getByText("지난 분기 실적 모으기")).toBeTruthy();
    expect(within(drawer).getByText("전망치 초안 쓰기")).toBeTruthy();
    // 담당 후보는 비어 있고, 참석자(정우성)가 목록 앞에 선다
    // 후보 목록은 드로어가 뜬 «뒤» 에 도착한다 — 기다리지 않으면 빈 목록을 읽는다
    const assignee = within(drawer).getByRole("button", { name: "담당 후보" });
    await waitFor(() => expect(assignee.textContent).toContain("선택"));
    fireEvent.click(assignee);
    const options = screen.getAllByRole("option").map((node) => node.textContent);
    expect(options[0]).toContain("정우성");
    // 업무가 아니라 요청 하나만 만들 수 있다
    expect(within(drawer).queryByRole("tab", { name: "업무" })).toBeNull();
  });

  it("이미 요청으로 선 후보는 「요청됨」으로만 남는다 — 누르는 자리를 두지 않는다", async () => {
    const promoted = {
      ...agenda,
      todos: [{ ...agenda.todos[0], linked: { work_request_id: "wr1", task_id: "task-9" } }],
    };
    renderDetail({}, [promoted]);
    expect(await screen.findByText("요청됨")).toBeTruthy();
    expect(screen.queryByText(/연관 업무/)).toBeNull();
    expect(screen.queryByRole("button", { name: "업무 생성" })).toBeNull();
    expect(screen.queryByRole("button", { name: "후보 빼기" })).toBeNull();
    // 그 줄은 목록에서 빠지지 않는다
    expect(screen.getByText("전망치 다시 뽑기")).toBeTruthy();
  });

  it("「실패」는 안내와 [다시 시도]를 내고 결론 표시를 내지 않는다", async () => {
    renderDetail({ status: "failed" });
    expect(await screen.findByText("회의 내용은 저장됐지만 글로 옮기지 못했습니다. 다시 시도할 수 있습니다.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeTruthy();
    expect(screen.queryByText("결론 남")).toBeNull();
    expect(screen.getAllByText("실패").length).toBeGreaterThan(0);
  });
});
