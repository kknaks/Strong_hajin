import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  getMeetingPromotionCandidates: vi.fn(),
  getTaskAssignmentCandidates: vi.fn(),
  getWorkRequestCcCandidates: vi.fn(),
  createWorkRequest: vi.fn(),
  createDirectTask: vi.fn(),
  assignTask: vi.fn(),
  getTasks: vi.fn(),
}));

import { ApiError } from "../../lib/api";
import * as api from "../../lib/api";
import { meetingScreen } from "../../lib/labels";
import type { MeetingAgenda, MeetingInfo, MeetingRecord } from "../../lib/viewModels";
import { useState } from "react";

import { MeetingDetailPage } from "./MeetingDetailPage";
import { resetRoster } from "./roster";

const agenda: MeetingAgenda = {
  agenda_id: "a1",
  last_saved_at: "2026-09-08T07:00:00Z",
  order: 1,
  title: "토큰 수요 전망",
  /* 이 파일은 종료·실패 화면이다 — 본문은 **최종 벌**이다 (§4.2-6).
     0.4.x 픽스처는 벌 축이 없어 한 안건이 세 트랙의 줄을 함께 들고 있었다 — 그 모양은 이제
     계약이 아니다(§4.2-9: 줄은 자기 벌의 안건에만 매달린다). 벌을 최종으로 두고 줄도 그 벌의 것만 남긴다. */
  track: "final" as const,
  title_placeholder: false,
  merged_from: [],
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
      evidence: [{ start_ms: 120_000, end_ms: 150_000 }], from_lines: [],
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
    can_edit_agendas: { memo: true, ai: false, final: true }, can_add_agenda: { memo: true, ai: false, final: true },
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
  /* 승격 후보는 **회의 전용 경로**가 낸다 — 조직 전체이고 **참석자가 앞** 이다 (D40).
     그 순서를 서버가 이미 지어서 주므로 픽스처도 그 모양이다: 참석자(정우성)가 먼저 온다.
     화면이 다시 정렬하지 않는다는 것을 이 순서가 증명한다 — 화면이 정렬하면 이 픽스처로도 통과해 버린다. */
  vi.mocked(api.getMeetingPromotionCandidates).mockResolvedValue([
    { id: "2", display_name: "정우성" },
    { id: "9", display_name: "한서린" },
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
    fireEvent.click(screen.getByRole("tab", { name: "스크립트" }));
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

    /* 줄은 «쌓인다» — 「시각 · 화자」 한 줄 위, 본문이 그 아래 열 전체 폭이다.
       예전에는 시각 | 화자 | 본문 세 칸이 가로로 서서 본문에 남는 폭이 200 이 안 됐다(현재 화면 17).
       ⚠ 이 화면(종료)의 디자인을 따로 바꾼 것이 아니라, 스크립트 줄이 진행 중과 «같은 부품» 이라
       함께 간 것이다 — 낼 값과 순서는 그대로다. */
    const first = within(script).getAllByRole("listitem")[0];
    expect(first.querySelector(".scax-script-line__at")?.textContent).toBe("01:00");
    expect(first.querySelector(".scax-script-line__who")?.textContent).toBe("화자 1");
    expect(first.querySelector(".scax-script-line__text")?.textContent).toBe("먼저 전제부터 맞춰 봅시다.");
  });

  /* v0.5.1: 출처는 **사람 벌만** 갖는다 (§4.1-2). 구 `"ai"` 값은 은퇴했다 — AI 가 세운 안건은
     출처가 아니라 «벌» 로 갈린다(`track === "ai"`). 그래서 넷이고, `null` 과 모르는 값은 자리가 서지 않는다.
     ⚠ 약하게 만든 것이 아니다: 「AI 정리」가 사라진 만큼 «null 이면 안 선다» 를 새로 건다. */
  it("안건 출처는 넷이고, null 이거나 모르는 값이면 그 자리가 서지 않는다", async () => {
    renderAfter({}, [
      { ...agenda, agenda_id: "s1", order: 1, title: "직접 쓴 것", source: "manual" },
      { ...agenda, agenda_id: "s2", order: 2, title: "세트에서", source: "set" },
      { ...agenda, agenda_id: "s3", order: 3, title: "지난 회의", source: "carried" },
      { ...agenda, agenda_id: "s4", order: 4, title: "다른 회의", source: "derived" },
      // AI 벌·최종 벌의 안건은 출처가 `null` 이다 — 그 자리가 서지 않는다
      { ...agenda, agenda_id: "s5", order: 5, title: "출처 없는 것", source: null },
      // 계약에 없는 값이 와도 화면이 깨지지 않는다 — 그 자리가 그냥 서지 않을 뿐이다
      { ...agenda, agenda_id: "s6", order: 6, title: "모르는 출처", source: "sideways" as MeetingAgenda["source"] },
    ]);
    await screen.findByText("DB ax 전략");
    // 완료된 회의에서도 출처는 사라지지 않는다 (E21 「상태와 무관하게 늘 낸다」)
    expect(screen.queryByText("AI 정리")).toBeNull();
    for (const label of ["직접 입력", "세트", "지난 회의에서 넘어옴", "다른 회의에서 파생"]) {
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
      lines: [{ ...agenda.lines[0], evidence: [{ start_ms: 140_000, end_ms: 145_000 }], from_lines: [] }],
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
      // 승격도 생성 계약을 지난다 — 이 제출 의도의 멱등 키가 함께 간다 (W1 Phase 5).
      }, expect.any(String)),
    );
    // 업무 요청을 직접 만들지 않는다 — 출처 두 열이 실려야 한다
    expect(api.createWorkRequest).not.toHaveBeenCalled();
    expect(await screen.findByText("요청됨")).toBeTruthy();
    // 회의록에 그 업무로 가는 링크를 두지 않는다
    expect(screen.queryByRole("button", { name: /연관 업무/ })).toBeNull();
    expect(onNotice).toHaveBeenCalled();
  });

  it("승격 담당 후보는 **회의 전용 경로**에서 온다 — 업무 배정 후보를 쓰지 않는다", async () => {
    /* 업무 관리의 후보 목록은 **누른 사람의 배정 권한**으로 좁히고 본인을 뺀다 — 실측에서 6명 중
       2명만 떴고, 배정 권한이 없는 사람은 **403** 을 받아 승격 자체를 못 했다. 승격의 요청 주체는
       회의(시스템)라 그 권한을 타면 안 된다 (D40). 그래서 경로가 다르다. */
    vi.mocked(api.getMeetingPromotionCandidates).mockResolvedValue([
      { id: "2", display_name: "정우성" },
      { id: "1", display_name: "이건학" },
      { id: "9", display_name: "한서린" },
    ]);
    renderAfter();
    await screen.findByText("전망치 다시 뽑기");
    fireEvent.click(screen.getByRole("button", { name: "업무 생성" }));
    const drawer = await screen.findByRole("dialog", { name: "업무 요청" });

    // 회의 id 를 실어 회의 전용 경로를 부른다
    await waitFor(() => expect(api.getMeetingPromotionCandidates).toHaveBeenCalledWith("m1"));
    // 업무 관리 쪽은 부르지 않는다 — 그 경로는 업무 화면의 것이다
    expect(api.getWorkRequestAssigneeCandidates).not.toHaveBeenCalled();
    expect(api.getTaskAssignmentCandidates).not.toHaveBeenCalled();

    const assignee = within(drawer).getByRole("button", { name: "담당 후보" });
    await waitFor(() => expect(assignee.textContent).toContain("선택"));
    fireEvent.click(assignee);
    const options = screen.getAllByRole("option").map((node) => node.textContent ?? "");
    // **본인도 목록에 있다** — 업무 배정 규칙은 본인을 빼지만 승격은 빼지 않는다
    expect(options.some((text) => text.includes("이건학"))).toBe(true);
    // 서버가 낸 순서 그대로다 — 화면이 다시 정렬하지 않는다
    expect(options.map((text) => text.trim())).toEqual(["정우성", "이건학", "한서린"]);
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
          lines: [{ line_id: "l9", track: "final", order: 1, text: "다른 사람이 먼저 쓴 줄.", author: null, at_ms: null, evidence: [], from_lines: [] }],
        },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("다른 곳에서 먼저 저장됐습니다. 지금 있는 내용으로 바꿔 두었습니다."));
    expect((await screen.findAllByLabelText("내용을 한 줄로 적으세요"))[0]).toHaveProperty("value", "다른 사람이 먼저 쓴 줄.");
    expect(screen.queryByDisplayValue("내가 고친 줄.")).toBeNull();
  });

  /* ──────────────────────────────────────────────────────────────────────────
     **최종 회의록만 회의록이다** (사용자 결정 2026-09-14).
     사람 벌·AI 벌은 «임시 재료» 다 — 회의가 끝난 뒤 회의록의 자리를 받는 것은 최종 벌뿐이다.
     받는 쪽이 합본(`agendas`)을 그대로 쓰면 임시가 최종인 척한다. 실물에서 난 자리가 아래 둘이다.
     ────────────────────────────────────────────────────────────────────────── */
  /** 같은 회의의 임시 두 벌 — 최종과 «다른 제목» 을 들어야 섞였는지 보인다. */
  const memoLeftover: MeetingAgenda = {
    ...agenda,
    agenda_id: "m-1",
    title: "회의 중에 적어 둔 메모 안건",
    track: "memo",
    concluded: false,
    lines: [],
    todos: [],
  };
  const aiLeftover: MeetingAgenda = { ...memoLeftover, agenda_id: "ai-1", title: "AI 가 세운 안건", track: "ai" };

  it("[다음 회의 예약]은 **최종 벌만** 담는다 — 임시 두 벌은 넘어가지 않는다", async () => {
    /* 실측(회의 `1ac58a4c…`): 화면엔 최종 2개인데 모달엔 5개가 담겼다 — `memo` 1 · `ai` 2 · `final` 2.
       `concluded` 만으로는 못 거른다: 결론 표시는 최종 벌에만 서므로(§4.0-5) 임시 두 벌은
       전부 `false` 로 통과한다. 그래서 **벌** 로 거른다. */
    const carryable: MeetingAgenda = { ...agenda, agenda_id: "f-2", title: "이어서 볼 최종 안건", concluded: false, lines: [], todos: [] };
    renderAfter({}, [agenda, carryable, memoLeftover, aiLeftover]);
    await screen.findByText("DB ax 전략");

    fireEvent.click(screen.getByRole("button", { name: "다음 회의 예약" }));
    const modal = await screen.findByRole("dialog", { name: /회의 예약/ });

    // 결론 안 난 «최종» 안건 하나만 담긴다
    expect(within(modal).getByText("이어서 볼 최종 안건")).toBeTruthy();
    // 임시 두 벌은 자리가 없다 — 회의록이 아니다
    expect(within(modal).queryByText("회의 중에 적어 둔 메모 안건")).toBeNull();
    expect(within(modal).queryByText("AI 가 세운 안건")).toBeNull();
    // 결론 난 최종 안건도 안 넘어간다 (끝난 일이다)
    expect(within(modal).queryByText("토큰 수요 전망")).toBeNull();
  });

  it("저장은 **최종 벌 안건만** 보낸다 — 임시 벌로 나가면 서버가 409 다", async () => {
    /* 줄 편집은 최종 벌의 일이다 (§8-9). 합본을 훑으면 사람 벌·AI 벌 안건에도 `PATCH` 가 나가고,
       서버는 최종이 아닌 안건에 줄을 실으면 409 를 낸다. */
    renderAfter({}, [agenda, memoLeftover, aiLeftover]);
    await screen.findByText("DB ax 전략");
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    fireEvent.change(screen.getAllByLabelText("내용을 한 줄로 적으세요")[0], { target: { value: "고친 줄." } });

    vi.mocked(api.updateMeetingAgenda).mockResolvedValue({ ...agenda });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(api.updateMeetingAgenda).toHaveBeenCalled());
    for (const call of vi.mocked(api.updateMeetingAgenda).mock.calls) {
      expect(call[1]).toBe("a1");
    }
  });

  /* ──────────────────────────────────────────────────────────────────────────
     **줄의 계보를 잃지 않는다** (§8-9). `PATCH` 의 `lines` 는 `{line_id?, text}` 목록이고,
     `line_id` 를 빠뜨린 줄은 서버가 **새 줄** 로 받는다 — 근거(`evidence`)와 `from_lines` 가
     그 자리에서 끊긴다. 한 줄만 고쳐도 나머지 줄의 id 가 함께 실려야 하는 이유다.
     ────────────────────────────────────────────────────────────────────────── */
  /** 줄 셋짜리 최종 안건 — 「하나만 고쳤을 때 나머지 둘」을 볼 수 있는 가장 작은 모양이다. */
  const threeLines: MeetingAgenda = {
    ...agenda,
    lines: [
      { line_id: "l1", track: "final", order: 1, text: "첫째 줄.", author: null, at_ms: null, evidence: [{ start_ms: 1_000, end_ms: 2_000 }], from_lines: ["a1"] },
      { line_id: "l2", track: "final", order: 2, text: "둘째 줄.", author: null, at_ms: null, evidence: [], from_lines: [] },
      { line_id: "l3", track: "final", order: 3, text: "셋째 줄.", author: null, at_ms: null, evidence: [], from_lines: [] },
    ],
  };

  it("줄 셋 중 하나만 고쳐 저장해도 **나머지 둘의 `line_id` 가 그대로 실린다**", async () => {
    renderAfter({}, [threeLines]);
    await screen.findByText("DB ax 전략");
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    fireEvent.change(screen.getAllByLabelText("내용을 한 줄로 적으세요")[1], { target: { value: "둘째 줄을 고쳤다." } });

    vi.mocked(api.updateMeetingAgenda).mockResolvedValue({ ...threeLines });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(api.updateMeetingAgenda).toHaveBeenCalled());
    const patch = vi.mocked(api.updateMeetingAgenda).mock.calls[0][2];
    expect(patch.lines).toEqual([
      { line_id: "l1", text: "첫째 줄." },
      { line_id: "l2", text: "둘째 줄을 고쳤다." },
      { line_id: "l3", text: "셋째 줄." },
    ]);
  });

  it("409 로 갈아 끼운 뒤 다시 저장해도 **서버가 준 `line_id` 가 실린다**", async () => {
    /* 충돌이 나면 화면은 서버가 낸 지금 줄로 갈아 끼운다. 그때 id 를 흘리면, 다음 저장이
       그 줄들을 전부 «새 줄» 로 밀어 넣어 근거가 통째로 끊긴다 — 되살리기가 불가능한 자리다. */
    renderAfter({}, [threeLines]);
    await screen.findByText("DB ax 전략");
    fireEvent.click(screen.getByRole("button", { name: "수정" }));
    fireEvent.change(screen.getAllByLabelText("내용을 한 줄로 적으세요")[0], { target: { value: "내가 고친 줄." } });

    const server: MeetingAgenda = {
      ...threeLines,
      last_saved_at: "2026-09-08T07:30:00Z",
      lines: [
        { line_id: "s1", track: "final", order: 1, text: "남이 먼저 쓴 첫 줄.", author: null, at_ms: null, evidence: [], from_lines: [] },
        { line_id: "s2", track: "final", order: 2, text: "남이 먼저 쓴 둘째 줄.", author: null, at_ms: null, evidence: [], from_lines: [] },
      ],
    };
    vi.mocked(api.updateMeetingAgenda).mockRejectedValueOnce(
      new ApiError(409, "Conflict", { code: "meeting_agenda_stale", current: server }),
    );
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => expect((screen.getAllByLabelText("내용을 한 줄로 적으세요")[0] as HTMLInputElement).value).toBe("남이 먼저 쓴 첫 줄."));

    // 갈아 끼운 줄 위에서 다시 고쳐 저장한다
    fireEvent.change(screen.getAllByLabelText("내용을 한 줄로 적으세요")[1], { target: { value: "그 위에 다시 고친다." } });
    vi.mocked(api.updateMeetingAgenda).mockResolvedValue(server);
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(vi.mocked(api.updateMeetingAgenda).mock.calls).toHaveLength(2));
    expect(vi.mocked(api.updateMeetingAgenda).mock.calls[1][2].lines).toEqual([
      { line_id: "s1", text: "남이 먼저 쓴 첫 줄." },
      { line_id: "s2", text: "그 위에 다시 고친다." },
    ]);
  });

  /* 「고치는 칸에 후보가 차 있다」는 절반은 자리를 옮겨 MeetingEditModal.test.tsx 가 든다 —
     이 화면에 남은 절반(후보를 흐리게 내고 제목인 척하지 않는다)만 여기서 잠근다. */
  it("제목 후보는 제목이 아니다 — 「제목 없는 회의」 옆에 후보로만 선다", async () => {
    renderAfter({ title: null, title_candidate: "DB AX 전환 범위 논의" });
    expect(await screen.findByText("제목 후보 DB AX 전환 범위 논의")).toBeTruthy();
    expect(screen.getByText("제목 없는 회의")).toBeTruthy();
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
      can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: false, ai: false, final: false },
    });
    await screen.findByText("DB ax 전략");
    expect(screen.getByRole("link", { name: "내보내기" })).toBeTruthy();
    /* 「회의 정보 수정」은 이 목록에서 뺐다 — 그 자리가 이 화면에서 목록 카드로 옮겨 갔다.
       그 잠금은 MeetingEditModal.test.tsx 가 든다. */
    for (const name of ["공유", "수정", "자료 첨부", "업무 생성", "후보 빼기", "다음 회의 예약"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    expect(screen.queryByRole("tab", { name: "첨부" })).toBeNull();
    expect(screen.getByRole("tab", { name: "스크립트" })).toBeTruthy();
  });

  it("「정리 중」에는 상태를 다시 묻고, 끝나면 묻기를 멈춘다", async () => {
    vi.useFakeTimers();
    try {
      const settling = { meeting: meeting({ status: "summarizing", can_edit_note: false, can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: false, ai: false, final: false } }), agendas: [agenda] };
      vi.mocked(api.readMeeting).mockResolvedValue(settling);
      renderAfter({ status: "summarizing", can_edit_note: false, can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: false, ai: false, final: false } });
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
    renderAfter({ status: "summarizing", can_edit_note: false, can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: false, ai: false, final: false } });
    await screen.findByText("DB ax 전략");
    expect(screen.getByText("정리하는 중")).toBeTruthy();
    /* 회의록 자리는 **도는 원 + 한 문장**이다. 스켈레톤 일곱 줄이던 것을 바꿨다 —
       스켈레톤은 「올 내용의 모양을 안다」는 자리인데 합성 결과가 몇 줄일지는 아무도 모른다.
       읽어 주는 자리(role=status · aria-busy)는 부품이 갖는다. */
    expect(screen.getByText(meetingScreen.finalNoteGenerating)).toBeTruthy();
    const body = document.querySelector(".scax-note__body") as HTMLElement;
    const busy = within(body).getAllByRole("status").find((one) => one.getAttribute("aria-busy") === "true");
    expect(busy).toBeTruthy();
    expect(busy?.querySelector(".scax-spinner")).toBeTruthy();
    /* 스켈레톤 부재는 «회의록 칸 안» 에서만 본다 — 문서 전체로 보면 아직 자료를 불러오는
       4칸의 스켈레톤이 걸려 간헐로 빨개진다(그 칸은 이 검사의 관심사가 아니다). */
    expect(body.querySelector(".scax-skeleton")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "스크립트" }));
    expect(await within(await screen.findByRole("list", { name: "스크립트" })).findByText("먼저 전제부터 맞춰 봅시다.")).toBeTruthy();
  });
});

/* ════════════════════════════════════════════════════════════════════════════
   종료 뒤 «합성이 도는 동안» (§8-2). 사용자가 실물 화면을 보고 발주한 것 (2026-09-14).
   스켈레톤 일곱 줄이 「무엇을 기다리는지 말하지 않는 긴 회색 줄」이었다(현재 화면 25).
   ════════════════════════════════════════════════════════════════════════════ */
describe("SCR-106 「정리 중」 — 최종 회의록을 짓는 동안", () => {
  it("회의 중의 「AI 요약이 곧 생성됩니다.」와 섞이지 않는다 — 이 자리는 종료 뒤다", async () => {
    renderAfter({ status: "summarizing", can_edit_note: false, can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: false, ai: false, final: false } });
    await screen.findByText("DB ax 전략");
    expect(screen.getByText(meetingScreen.finalNoteGenerating)).toBeTruthy();
    // 회의 «중» 의 문구가 여기 서면 회의가 아직 도는 것처럼 읽힌다
    expect(screen.queryByText(meetingScreen.aiSummaryPending)).toBeNull();
  });

  it("정리 중에 새로고침해도 같은 안내가 선다 — 상태가 정본이다", async () => {
    // 「새로고침」 = 스트림도 배치도 없이 상세만 다시 읽고 들어온 창이다
    renderAfter({ status: "summarizing", can_edit_note: false, can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: false, ai: false, final: false } });
    const line = await screen.findByText(meetingScreen.finalNoteGenerating);
    // 스켈레톤 부재는 «회의록 칸 안» 에서만 본다 (위 검사와 같은 이유)
    const body = line.closest(".scax-note__body") as HTMLElement;
    expect(body).toBeTruthy();
    expect(body.querySelector(".scax-skeleton")).toBeNull();
  });

  it("정리 중에는 **어느 벌도 열리지 않는다** — 화면이 아니라 서버가 닫는다", async () => {
    /* 화면이 들고 있던 `&& !live && !settling` 를 걷었다 (백엔드 보고 §6-4). 서버의
       `NOTE_EDITABLE_STATUSES` 가 「종료 · 실패 · 취소」뿐이라 「정리 중」은 원래 거짓이고,
       안건 게이트 셋도 「정리 중」을 어느 집합에도 넣지 않는다. 같은 규칙을 두 곳이 말하지 않게
       걷었으므로, **서버 값 하나로 닫히는지**를 여기서 건다. */
    renderAfter({ status: "summarizing", can_edit_note: false, can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: false, ai: false, final: false } });
    await screen.findByText("DB ax 전략");
    expect(screen.queryByRole("button", { name: "수정" })).toBeNull();
    expect(screen.queryByRole("button", { name: "저장" })).toBeNull();
    expect(screen.queryByRole("button", { name: meetingScreen.dropAgenda })).toBeNull();
    expect(screen.queryByLabelText(meetingScreen.agendaPlaceholder)).toBeNull();
  });

  it("합성이 끝나면 진짜 회의록으로 바뀐다 — 프론트 타이머가 성공을 지어내지 않는다", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(api.readMeeting).mockResolvedValue({
        meeting: meeting({ status: "summarizing", can_edit_note: false, can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: false, ai: false, final: false } }),
        agendas: [agenda],
      });
      renderAfter({ status: "summarizing", can_edit_note: false, can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: false, ai: false, final: false } });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(screen.getByText(meetingScreen.finalNoteGenerating)).toBeTruthy();

      /* 시간만 흘려서는 아무 일도 일어나지 않는다 — 서버가 「종료」라고 말해야 바뀐다.
         (여기서 상태를 안 바꾸고 시간만 밀면 안내가 그대로 서 있다) */
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
      expect(screen.getByText(meetingScreen.finalNoteGenerating)).toBeTruthy();

      vi.mocked(api.readMeeting).mockResolvedValue({ meeting: meeting(), agendas: [agenda] });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
      expect(screen.queryByText(meetingScreen.finalNoteGenerating)).toBeNull();
      expect(screen.getByText("수요는 구조적으로 는다는 전제에 합의했다.")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("합성이 깨지면 실패 안내와 [다시 시도]로 바뀐다 — 영원히 도는 원이 없다 (§8-8)", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(api.readMeeting).mockResolvedValue({
        meeting: meeting({ status: "summarizing", can_edit_note: false, can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: false, ai: false, final: false } }),
        agendas: [agenda],
      });
      renderAfter({ status: "summarizing", can_edit_note: false, can_edit_agendas: { memo: false, ai: false, final: false }, can_add_agenda: { memo: false, ai: false, final: false } });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(screen.getByText(meetingScreen.finalNoteGenerating)).toBeTruthy();

      vi.mocked(api.readMeeting).mockResolvedValue({
        meeting: meeting({ status: "failed", failure_reason: "재전사가 끊겼습니다" }),
        agendas: [agenda],
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });

      expect(screen.queryByText(meetingScreen.finalNoteGenerating)).toBeNull();
      expect(screen.getByText(meetingScreen.convertFailed)).toBeTruthy();
      // 사유를 그대로 낸다 — 지어낸 말로 덮지 않는다
      expect(screen.getByText("재전사가 끊겼습니다")).toBeTruthy();
      expect(screen.getByRole("button", { name: meetingScreen.retry })).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });
});
