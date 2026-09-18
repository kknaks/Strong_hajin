import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async (actual) => ({
  getTaskAssignments: vi.fn(),
  getTaskProposals: vi.fn(),
  createTaskProposal: vi.fn(),
  respondTaskProposal: vi.fn(),
  withdrawTaskProposal: vi.fn(),
  reopenTask: vi.fn(),
  getTaskChildren: vi.fn(),
  withdrawWorkRequest: vi.fn(),
  hideWorkRequestListEntry: vi.fn(),
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
  getMeetingPromotionCandidates: vi.fn(),
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
  /* 이 파일의 기본 회의는 「종료」다 — 그 화면이 내는 것은 **최종 벌**이다 (§4.2-6).
     0.4.x 픽스처는 벌 축이 없어 한 안건이 세 트랙의 줄을 함께 들고 있었다 — 그 모양은 이제
     계약이 아니다(§4.2-9: 줄은 자기 벌의 안건에만 매달린다). 벌을 최종으로 두고 줄도 그 벌의 것만 남긴다. */
  track: "final" as const,
  title_placeholder: false,
  merged_from: [],
  source: "carried",
  concluded: true,
  lines: [
    { line_id: "l1", track: "final", order: 1, text: "수요는 구조적으로 는다는 전제에 합의했다.", author: "AI", at_ms: null, evidence: [{ start_ms: 120_000, end_ms: 150_000 }], from_lines: [] },
    { line_id: "l2", track: "memo", order: 1, text: "분기별로 다시 뽑기로.", author: "이건학", at_ms: null, evidence: [], from_lines: [] },
    // AI 가 낸 줄과 합쳐진 최종 줄에는 작성자가 없다 — 서버가 null 로 낸다 (W9)
    { line_id: "l3", track: "ai", order: 1, text: "대응은 확장과 효율 두 축이다.", author: null, at_ms: null, evidence: [], from_lines: [] },
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
    can_edit_agendas: { memo: true, ai: false, final: true }, can_add_agenda: { memo: true, ai: false, final: true },
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

function renderDetail(
  over: Partial<MeetingInfo> = {},
  agendas: MeetingAgenda[] = [agenda],
  extra: Partial<Parameters<typeof MeetingDetailPage>[0]> = {},
) {
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
      {...extra}
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
  /* 승격 후보는 **회의 전용 경로**가 낸다 — 조직 전체이고 **참석자가 앞** 이다 (D40).
     그 순서를 서버가 이미 지어서 주므로 픽스처도 그 모양이다: 참석자(정우성)가 먼저 온다.
     화면이 다시 정렬하지 않는다는 것을 이 순서가 증명한다 — 화면이 정렬하면 이 픽스처로도 통과해 버린다. */
  vi.mocked(api.getMeetingPromotionCandidates).mockResolvedValue([
    { id: "2", display_name: "정우성" },
    { id: "9", display_name: "한서린" },
  ]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SCR-106 회의 상세 — 상태와 관계가 무엇을 낼지 정한다", () => {
  /*
   * 시안 10 의 상세 머리는 «제목» 으로 시작한다 — 그 위의 「회의 정보」 구획 라벨과 연필이 함께
   * 걷혔고, 고치는 자리는 목록 카드의 [수정] → `MeetingEditModal` 로 갔다.
   * 그 자리의 잠금(칸 · 「바뀐 것이 없으면 저장 못 한다」 · `updateMeetingInfo` patch 모양 ·
   * 고치던 채로 닫을 때 묻기 · 서버가 닫은 회의)은 `MeetingEditModal.test.tsx` 가 통째로 든다.
   * 여기서는 **이 화면에 남은 것**을 잠근다.
   */
  it("상태 여섯 어디서도 머리는 제목으로 시작한다 — 구획 라벨도 연필도 없다", async () => {
    for (const status of ["scheduled", "in_progress", "summarizing", "done", "failed", "cancelled"] as MeetingStatus[]) {
      renderDetail({ status });
      await screen.findByText("DB ax 전략");
      expect(screen.queryByText("회의 정보")).toBeNull();
      // 제목은 머리의 첫 줄이다 — 위에 얹힌 줄이 없다
      const head = document.querySelector(".scax-detail__head") as HTMLElement;
      expect(head.firstElementChild?.querySelector(".scax-detail__title")?.textContent).toBe("DB ax 전략");
      cleanup();
    }
  });

  it("[회의 시작]과 집중 모드는 제목과 «같은 줄» 오른쪽에 선다 (시안 10)", async () => {
    // 집중 모드는 워크스페이스가 접는 판단을 갖는다 — 넘겨줄 때만 그 자리가 선다
    renderDetail({ status: "scheduled" }, [agenda], { onToggleFocus: vi.fn() });
    await screen.findByText("DB ax 전략");
    const row = document.querySelector(".scax-detail__title-row") as HTMLElement;
    expect(within(row).getByRole("button", { name: /회의 시작/ })).toBeTruthy();
    // 집중 모드는 [회의 시작] 왼쪽이다 — 시안이 쓰는 대각선 양방향 화살표 글리프다
    expect(within(row).getByRole("button", { name: "회의에 집중하기" })).toBeTruthy();
  });

  it("「예정」은 [수정] 없이 안건 칸이 늘 서고 안건을 더하고 뺀다 — 줄 편집 칸은 서지 않는다", async () => {
    // BE 가 갈라 내는 두 필드: 「예정」은 안건만 열리고 회의록 줄은 닫혀 있다
    /* 「예정」 화면이 내는 것은 **사람 벌**이다 (§4.2-6) — 최종 벌은 아직 없다.
       게이트도 사람 벌 칸만 열린다: 예정에서 최종 벌은 닫혀 있다. */
    renderDetail(
      { status: "scheduled", can_edit_note: false, can_edit_agendas: { memo: true, ai: false, final: false }, can_add_agenda: { memo: true, ai: false, final: false } },
      [{ ...agenda, track: "memo", source: "carried", lines: [] }],
    );
    await screen.findByText("DB ax 전략");
    /* 시안 10: 회의 전에는 안건 목록 «바로 아래» 에 입력 칸과 [안건 추가]가 그냥 서 있다.
       [수정]이 하던 일이 그 칸을 펴는 것 하나였으므로 단추를 내리고 칸을 상시로 뒀다 —
       할 수 있는 일(더하기·빼기)은 그대로다. */
    expect(screen.queryByRole("button", { name: "수정" })).toBeNull();
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

  it("안건 20 한도는 **벌마다** 센다 — 세 벌 합산으로 세지 않는다", async () => {
    /* 실측(`MeetingDetailPage.tsx:1031`): `agendas.length >= 20` 이 세 벌 합본을 셌다.
       사람 벌이 19개뿐인데 AI 벌·최종 벌이 합쳐 27개가 되어 [안건 추가]가 죽었다 —
       사람은 자기 벌에 한 자리가 남았는데도 더할 수가 없었다. */
    const many = (track: MeetingAgenda["track"], count: number, from: number): MeetingAgenda[] =>
      Array.from({ length: count }, (_, index) => ({
        ...agenda,
        agenda_id: `${track}-${from + index}`,
        order: from + index,
        title: `${track} 안건 ${from + index}`,
        track,
        lines: [],
        todos: [],
      }));

    renderDetail(
      { status: "scheduled", can_edit_note: false, can_edit_agendas: { memo: true, ai: false, final: false }, can_add_agenda: { memo: true, ai: false, final: false } },
      // 사람 벌 19 + AI 벌 5 + 최종 벌 5 = 합산 29. 합산으로 세면 여기서 이미 죽는다
      [...many("memo", 19, 1), ...many("ai", 5, 1), ...many("final", 5, 1)],
    );
    await screen.findByText("DB ax 전략");

    fireEvent.change(screen.getByLabelText("안건을 적으세요"), { target: { value: "스무 번째 안건" } });
    expect(screen.getByRole("button", { name: "안건 추가" })).not.toHaveProperty("disabled", true);
  });

  it("그 벌이 20을 채우면 더는 못 더한다 — 한도 자체는 산다", async () => {
    const memoFull: MeetingAgenda[] = Array.from({ length: 20 }, (_, index) => ({
      ...agenda,
      agenda_id: `memo-${index + 1}`,
      order: index + 1,
      title: `사람 벌 안건 ${index + 1}`,
      track: "memo" as const,
      lines: [],
      todos: [],
    }));
    renderDetail(
      { status: "scheduled", can_edit_note: false, can_edit_agendas: { memo: true, ai: false, final: false }, can_add_agenda: { memo: true, ai: false, final: false } },
      memoFull,
    );
    await screen.findByText("DB ax 전략");

    fireEvent.change(screen.getByLabelText("안건을 적으세요"), { target: { value: "스물한 번째" } });
    expect(screen.getByRole("button", { name: "안건 추가" })).toHaveProperty("disabled", true);
  });

  it("고칠 권한이 둘 다 없으면 [수정] 자체가 서지 않는다", async () => {
    renderDetail({ can_edit_note: false, can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: false, ai: false, final: false } });
    await screen.findByText("DB ax 전략");
    expect(screen.queryByRole("button", { name: "수정" })).toBeNull();
  });

  it("작성자가 없는 줄(AI · 최종)이 섞여도 화면이 터지지 않는다", async () => {
    renderDetail({}, [
      {
        ...agenda,
        lines: [
          { line_id: "n1", track: "final", order: 1, text: "AI 가 합친 줄.", author: null, at_ms: null, evidence: [], from_lines: [] },
          { line_id: "n2", track: "memo", order: 2, text: "사람이 적은 줄.", author: "이건학", at_ms: null, evidence: [], from_lines: [] },
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
            evidence: [{ start_ms: Number.NaN, end_ms: Number.NaN }, { start_ms: 120_000, end_ms: 150_000 }], from_lines: [],
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
    /* **저장이 줄마다 `line_id` 를 싣는다** (§8-9). 글자 배열은 이제 422 이고, id 를 빠뜨리면
       읽어 온 줄이 전부 «새 줄» 로 다시 저장돼 계보가 죽는다. 빈 줄은 보내기 전에 버린다 —
       [내용 줄 추가]로 만든 빈 줄이 페이로드에 없는 것이 그 증거다. */
    await waitFor(() =>
      expect(api.updateMeetingAgenda).toHaveBeenCalledWith("m1", "a1", {
        lines: [{ line_id: "l1", text: "수요는 구조적으로 는다는 전제에 합의했다." }],
        expected_last_saved_at: "2026-09-08T07:00:00Z",
      }),
    );
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("저장했습니다."));
  });

  it("공유받은 사람에게는 조작 버튼이 하나도 없다 — 회의록과 내보내기만 남는다", async () => {
    renderDetail({ viewer_relation: "shared", can_edit_info: false, can_edit_note: false, can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: false, ai: false, final: false } });
    await screen.findByText("DB ax 전략");
    const exportLink = screen.getByRole("link", { name: "내보내기" });
    expect(exportLink.getAttribute("href")).toBe("/api/meetings/m1/export?format=html");
    /* 「회의 정보 수정」은 이 목록에서 뺐다 — 그 자리가 이 화면에서 «사라져» 목록 카드로 갔으므로
       여기서 「없다」고 세어 봐야 권한을 말해 주지 않는다. 그 잠금은 MeetingEditModal.test.tsx 가 든다. */
    for (const name of ["공유", "수정", "자료 첨부", "회의 시작", "업무 생성"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    // 자료 탭도 서지 않는다 — 노출 권한이 참석자다
    expect(screen.queryByRole("tab", { name: "첨부" })).toBeNull();
    expect(screen.getByRole("tab", { name: "스크립트" })).toBeTruthy();
  });

  it("「예정」에도 스크립트 탭은 서고 «아직 없다» 를 낸다 — [공유]는 서지 않는다", async () => {
    /* 시안 08 의 레일 머리는 상태와 무관하게 탭 둘이다. 계약도 그 말과 맞는다 —
       `GET /transcript` 는 아직 아무 말도 없는 회의에 빈 목록을 준다. 탭을 지우면
       「아직 없다」와 「볼 수 없다」가 화면에서 같은 모양이 된다. */
    renderDetail({ status: "scheduled" });
    await screen.findByText("DB ax 전략");
    fireEvent.click(screen.getByRole("tab", { name: "스크립트" }));
    expect(await screen.findByText("아직 원문이 없습니다.")).toBeTruthy();
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
    expect(within(drawer).getByText("상대가 수락해야 그 사람의 업무가 됩니다. 수락 전에는 담당이 서지 않습니다.")).toBeTruthy();
    expect((within(drawer).getByLabelText("요청할 업무") as HTMLInputElement).value).toBe("전망치 다시 뽑기");
    expect(within(drawer).getByDisplayValue("분기별 전망치를 다시 뽑아 다음 회의에 올린다.")).toBeTruthy();
    // 기한 칸은 입력칸이 아니라 달력을 여는 트리거다 (DS-17) — 미리 채운 값은 그 글자로 선다
    expect(within(drawer).getByRole("button", { name: "기한 달력 열기" }).textContent).toBe("2026-09-12");
    expect(within(drawer).getByText("지난 분기 실적 모으기")).toBeTruthy();
    expect(within(drawer).getByText("전망치 초안 쓰기")).toBeTruthy();
    // 담당 후보는 비어 있고, **서버가 앞에 둔** 참석자(정우성)가 목록 앞에 선다
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
