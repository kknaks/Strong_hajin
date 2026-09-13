import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async (actual) => ({
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
import type { MeetingAgenda, MeetingInfo, MeetingMaterial, MeetingRecord } from "../../lib/viewModels";
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
  lines: [],
  todos: [],
};

const mine: MeetingMaterial = {
  material_id: "mat-1",
  name: "DB AX 전환 범위 초안.pdf",
  content_type: "application/pdf",
  size: 2_500_000,
  uploaded_by: "1",
  uploaded_at: "2026-09-08T06:34:00Z",
  can_detach: true,
};
const theirs: MeetingMaterial = {
  material_id: "mat-2",
  name: "토큰 비용 시나리오.md",
  content_type: "text/markdown",
  size: 18_000,
  uploaded_by: "2",
  uploaded_at: "2026-09-08T06:47:00Z",
  // 남이 올린 자료는 서버가 「못 뗀다」고 말한다
  can_detach: false,
};

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

function renderDetail(over: Partial<MeetingInfo> = {}) {
  const value: MeetingRecord = { meeting: meeting(over), agendas: [agenda] };
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

/** 고르는 시점의 판정을 통과하는 파일 하나. */
function pdf(name = "붙는 자료.pdf", size = 1_000) {
  return new File(["x"], name, { type: "application/pdf" });
}

async function openAttach() {
  renderDetail();
  await screen.findByText("DB ax 전략");
  fireEvent.click(await screen.findByRole("button", { name: /자료 첨부/ }));
  return screen.getByRole("dialog", { name: "자료 첨부" });
}

beforeEach(() => {
  resetRoster();
  vi.mocked(api.readMeetingRooms).mockResolvedValue([]);
  vi.mocked(api.getOrganizationTree).mockResolvedValue([]);
  vi.mocked(api.getOrganizationUnitMembers).mockResolvedValue([]);
  vi.mocked(api.getTasks).mockResolvedValue([]);
  vi.mocked(api.readMeetingTranscript).mockResolvedValue({ items: [], memos: [] });
  vi.mocked(api.readMeetingMaterials).mockResolvedValue([mine, theirs]);
  vi.mocked(api.readMeetingShares).mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SCR-106 자료 (MOD-104 · WP-005)", () => {
  it("떼는 것은 올린 사람뿐이다 — 남이 올린 자료엔 × 가 없다", async () => {
    renderDetail();
    await screen.findByText(mine.name);
    const list = screen.getByRole("list", { name: "자료" });
    const rows = within(list).getAllByRole("listitem");
    expect(within(rows[0]).getByRole("button", { name: "파일 빼기" })).toBeTruthy();
    expect(within(rows[1]).queryByRole("button", { name: "파일 빼기" })).toBeNull();

    vi.mocked(api.detachMeetingMaterial).mockResolvedValue(undefined);
    fireEvent.click(within(rows[0]).getByRole("button", { name: "파일 빼기" }));
    fireEvent.click(screen.getByRole("button", { name: "삭제" }));
    await waitFor(() => expect(api.detachMeetingMaterial).toHaveBeenCalledWith("m1", "mat-1"));
  });

  it("「진행 중」에는 첨부도 삭제도 서지 않는다", async () => {
    // 서버가 「진행 중」에는 can_detach 를 내리지 않는다 — 화면은 그 값을 그대로 따른다
    vi.mocked(api.readMeetingMaterials).mockResolvedValue([{ ...mine, can_detach: false }, theirs]);
    renderDetail({ status: "in_progress", can_write_memo: false });
    await screen.findByText("DB ax 전략");
    expect(screen.queryByRole("button", { name: /자료 첨부/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "파일 빼기" })).toBeNull();
  });

  it("여럿을 한 번에 놓으면 되는 것만 붙고 안 되는 것은 사유와 함께 남는다", async () => {
    const drawer = await openAttach();
    fireEvent.change(within(drawer).getByLabelText("파일 선택"), {
      target: { files: [pdf("붙는 자료.pdf"), pdf("같이 보낸 자료.pdf")] },
    });

    vi.mocked(api.attachMeetingMaterials).mockResolvedValue({
      attached: [mine],
      failed: [{ name: "같이 보낸 자료.pdf", reason: "too_large" }],
    });
    fireEvent.click(within(drawer).getByRole("button", { name: "첨부" }));

    // 붙은 것은 목록에서 빠지고 못 붙은 것만 사유와 함께 남는다 — 모달도 닫지 않는다
    expect(await screen.findByText("20MB가 넘는 파일은 첨부할 수 없습니다.")).toBeTruthy();
    expect(screen.getByText("일부 파일을 올리지 못했습니다. 아래에서 확인해 주세요.")).toBeTruthy();
    expect(screen.getByText("같이 보낸 자료.pdf")).toBeTruthy();
    expect(within(drawer).queryByText("붙는 자료.pdf")).toBeNull();
    await waitFor(() => expect(api.readMeetingMaterials).toHaveBeenCalledTimes(2));
  });

  it("한 건도 못 붙으면 422 의 사유를 그 자리에 낸다", async () => {
    const drawer = await openAttach();
    fireEvent.change(within(drawer).getByLabelText("파일 선택"), { target: { files: [pdf("전부 막힌 자료.pdf")] } });

    vi.mocked(api.attachMeetingMaterials).mockRejectedValue(
      new ApiError(422, "Unprocessable Entity", {
        code: "meeting_materials_rejected",
        failed: [{ name: "전부 막힌 자료.pdf", reason: "unsupported_type" }],
      }),
    );
    fireEvent.click(within(drawer).getByRole("button", { name: "첨부" }));

    expect(await screen.findByText("첨부할 수 없는 형식입니다. 문서 · 이미지 · 압축 파일을 올려 주세요.")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "자료 첨부" })).toBeTruthy();
  });

  it("고르는 시점에도 크기와 형식을 본다 — 다 올리고 나서 듣지 않는다", async () => {
    const drawer = await openAttach();
    fireEvent.change(within(drawer).getByLabelText("파일 선택"), {
      target: { files: [new File(["x"], "구조도 원본.png", { type: "image/png" })] },
    });
    expect(within(drawer).getByText("첨부할 수 없는 형식입니다. 문서 · 이미지 · 압축 파일을 올려 주세요.")).toBeTruthy();
    // 보낼 것이 없으면 [첨부]가 서지 않는다
    expect(within(drawer).getByRole("button", { name: "첨부" }).hasAttribute("disabled")).toBe(true);
    expect(api.attachMeetingMaterials).not.toHaveBeenCalled();
  });
});

describe("SCR-106 공유 (MOD-105 · WP-005)", () => {
  const viewers = [
    { member_id: "1", name: "이건학", basis: "attendee" as const },
    { member_id: "2", name: "정우성", basis: "attendee" as const },
    { member_id: "9", name: "한지우", basis: "share" as const },
  ];

  async function openShare() {
    vi.mocked(api.readMeetingShares).mockResolvedValue(viewers);
    vi.mocked(api.getOrganizationTree).mockResolvedValue([
      { id: "u1", name: "제품본부", parent_id: null, unit_type: null, lifecycle: "active", display_order: 1, member_count: 3, direct_member_count: 3, leaders: [] },
    ]);
    vi.mocked(api.getOrganizationUnitMembers).mockResolvedValue([
      { member_id: "2", display_name: "정우성", memberships: [], positions: [], grade: "과장", jobs: [] },
      { member_id: "9", display_name: "한지우", memberships: [], positions: [], grade: "차장", jobs: [] },
      { member_id: "7", display_name: "오세림", memberships: [], positions: [], grade: "선임", jobs: [] },
    ]);
    renderDetail();
    await screen.findByText("DB ax 전략");
    fireEvent.click(screen.getByRole("button", { name: "공유" }));
    return screen.getByRole("dialog", { name: "공유" });
  }

  it("[삭제]는 공유로 들어온 사람에게만 붙는다 — 참석은 여기서 거두지 않는다", async () => {
    const modal = await openShare();
    const table = await within(modal).findByRole("table");
    // 목록은 표가 선 «뒤» 에 도착한다 — 기다리지 않으면 빈 tbody 를 읽는다
    expect(await within(table).findAllByText("참석")).toHaveLength(2);
    expect(within(table).getAllByText("열람")).toHaveLength(1);
    expect(within(table).getAllByRole("button", { name: "삭제" })).toHaveLength(1);

    // 거둔 뒤의 목록이 그대로 돌아온다 — 화면이 다시 묻지 않는다
    vi.mocked(api.revokeMeetingShare).mockResolvedValue(viewers.slice(0, 2));
    fireEvent.click(within(table).getByRole("button", { name: "삭제" }));
    fireEvent.click(screen.getAllByRole("button", { name: "삭제" }).at(-1) as HTMLElement);
    await waitFor(() => expect(api.revokeMeetingShare).toHaveBeenCalledWith("m1", "9"));
    // 목록은 응답으로 갱신된다 — 다시 읽지 않는다
    await waitFor(() => expect(within(table).queryAllByText("열람")).toHaveLength(0));
    expect(api.readMeetingShares).toHaveBeenCalledTimes(1);
  });

  it("부서마다 «자기» 인원수를 낸다 — 부서를 골라도 다른 줄의 수가 바뀌지 않는다", async () => {
    vi.mocked(api.readMeetingShares).mockResolvedValue(viewers);
    vi.mocked(api.getOrganizationTree).mockResolvedValue([
      { id: "u1", name: "법무팀", parent_id: null, unit_type: null, lifecycle: "active", display_order: 1, member_count: 1, direct_member_count: 1, leaders: [] },
      { id: "u2", name: "플랫폼실", parent_id: null, unit_type: null, lifecycle: "active", display_order: 2, member_count: 3, direct_member_count: 3, leaders: [] },
    ]);
    vi.mocked(api.getOrganizationUnitMembers).mockImplementation(async (unitId: string) =>
      unitId === "u1"
        ? [{ member_id: "31", display_name: "오세림", memberships: [], positions: [], grade: "선임", jobs: [] }]
        : [
            { member_id: "41", display_name: "한서린", memberships: [], positions: [], grade: "주임", jobs: [] },
            { member_id: "42", display_name: "전지우", memberships: [], positions: [], grade: "본부장", jobs: [] },
            // 이미 볼 수 있는 사람은 세지 않는다 — 그 규칙은 그대로다
            { member_id: "9", display_name: "한지우", memberships: [], positions: [], grade: "차장", jobs: [] },
          ],
    );
    renderDetail();
    await screen.findByText("DB ax 전략");
    fireEvent.click(screen.getByRole("button", { name: "공유" }));
    const modal = screen.getByRole("dialog", { name: "공유" });

    const countOf = (unitName: string) =>
      (within(modal).getByText(unitName).closest("button") as HTMLElement).textContent?.replace(unitName, "").trim();

    await waitFor(() => expect(countOf("법무팀")).toBe("1명"));
    expect(countOf("플랫폼실")).toBe("2명");

    // 부서를 골라도 «다른» 줄의 수는 그대로다 — 고른 부서의 명단 길이가 전부에 퍼지지 않는다
    fireEvent.click(within(modal).getByText("플랫폼실"));
    expect(countOf("법무팀")).toBe("1명");
    expect(countOf("플랫폼실")).toBe("2명");
    fireEvent.click(within(modal).getByText("법무팀"));
    expect(countOf("법무팀")).toBe("1명");
    expect(countOf("플랫폼실")).toBe("2명");
  });

  it("이미 볼 수 있는 사람은 고르는 자리에 안 낸다 — 참석이든 열람이든", async () => {
    const modal = await openShare();
    await within(modal).findByRole("table");
    // 조직도에는 아직 못 보는 사람만 선다
    expect(within(modal).getByText("오세림")).toBeTruthy();
    fireEvent.change(within(modal).getByRole("combobox"), { target: { value: "정우성" } });
    expect(within(modal).getByText("찾는 사람이 없습니다.")).toBeTruthy();
    fireEvent.change(within(modal).getByRole("combobox"), { target: { value: "한지우" } });
    expect(within(modal).getByText("찾는 사람이 없습니다.")).toBeTruthy();
  });

  it("여러 명을 한 번에 열고, 알림 문구를 내지 않는다", async () => {
    const { onNotice } = renderDetail();
    vi.mocked(api.readMeetingShares).mockResolvedValue(viewers);
    vi.mocked(api.getOrganizationTree).mockResolvedValue([
      { id: "u1", name: "제품본부", parent_id: null, unit_type: null, lifecycle: "active", display_order: 1, member_count: 1, direct_member_count: 1, leaders: [] },
    ]);
    vi.mocked(api.getOrganizationUnitMembers).mockResolvedValue([
      { member_id: "7", display_name: "오세림", memberships: [], positions: [], grade: "선임", jobs: [] },
    ]);
    await screen.findByText("DB ax 전략");
    fireEvent.click(screen.getByRole("button", { name: "공유" }));
    const modal = screen.getByRole("dialog", { name: "공유" });
    fireEvent.click(await within(modal).findByText("오세림"));

    vi.mocked(api.shareMeetingWith).mockResolvedValue([...viewers, { member_id: "7", name: "오세림", basis: "share" }]);
    fireEvent.click(within(modal).getByRole("button", { name: "공유" }));
    await waitFor(() => expect(api.shareMeetingWith).toHaveBeenCalledWith("m1", ["7"]));
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("공유했습니다."));
    expect(onNotice.mock.calls.flat().join(" ")).not.toContain("알림");
  });

  it("공유받은 사람에겐 자료 탭도 공유도 없다", async () => {
    renderDetail({ viewer_relation: "shared", can_edit_info: false, can_edit_note: false, can_edit_agendas: false });
    await screen.findByText("DB ax 전략");
    expect(screen.queryByRole("button", { name: "자료" })).toBeNull();
    expect(screen.queryByRole("button", { name: "공유" })).toBeNull();
    expect(screen.queryByRole("button", { name: /자료 첨부/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "파일 빼기" })).toBeNull();
  });
});
