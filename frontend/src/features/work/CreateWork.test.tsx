import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", () => ({
  getTaskAssignments: vi.fn(),
  getTaskProposals: vi.fn(),
  createTaskProposal: vi.fn(),
  respondTaskProposal: vi.fn(),
  withdrawTaskProposal: vi.fn(),
  reopenTask: vi.fn(),
  getTaskChildren: vi.fn(),
  withdrawWorkRequest: vi.fn(),
  hideWorkRequestListEntry: vi.fn(),
  getWorkRequestAssigneeCandidates: vi.fn(),
  createDirectTask: vi.fn(),
  // 우 레일의 회의 절반 (증보 K23) — 업무 목록과 **다른 질의**다.
  getCalendar: vi.fn().mockResolvedValue([]),
  addTaskReference: vi.fn(),
  releaseTaskReference: vi.fn(),
  createWorkRequest: vi.fn(),
  assignTask: vi.fn(),
  getTask: vi.fn(),
  getTaskMaterials: vi.fn(),
  addChecklistItem: vi.fn(),
  reorderChecklist: vi.fn(),
  updateChecklistItem: vi.fn(),
  removeChecklistItem: vi.fn(),
  uploadTaskMaterial: vi.fn(),
  detachTaskMaterial: vi.fn(),
  attachTaskMaterialLink: vi.fn(),
  attachTaskMaterialReference: vi.fn(),
  getTasks: vi.fn(),
  listProjects: vi.fn(),
  getTaskAssignmentCandidates: vi.fn(),
  reassignTask: vi.fn(),
  taskMaterialContentUrl: () => "",
  addWorkRequestComment: vi.fn(),
  getWorkRequestTimeline: vi.fn(),
  getTaskHistory: vi.fn(),
  getTaskHistoryDiff: vi.fn(),
  uploadCommentAttachment: vi.fn(),
  uploadRequestEvidence: vi.fn(),
  requestAttachmentUrl: () => "",
  resubmitWorkRequest: vi.fn(),
  decideWorkRequest: vi.fn(),
  negotiateWorkRequest: vi.fn(),
  amendWorkRequest: vi.fn(),
}));

import * as api from "../../lib/api";
import { CreateWorkModal } from "./WorkModals";

const jiho = { id: "jiho", display_name: "지호 (팀장)", role: "manager" } as never;

function renderDrawer(projectCandidates: Array<{ project_id: string; name: string }> = []) {
  const onCreated = vi.fn();
  const rendered = render(
    <CreateWorkModal
      assigneeCandidates={[jiho]}
      assignCandidates={[jiho]}
      canCreateRequest
      canCreateTask
      ccCandidates={[]}
      onClose={vi.fn()}
      onCreated={onCreated}
      onError={vi.fn()}
      ownerName="민아"
      projectCandidates={projectCandidates as never}
    />,
  );
  return { ...rendered, onCreated };
}

/**
 * 최종 발주 2 — 생성 모달은 **왼쪽 세로 탭**이다. 필드는 그대로이고 어느 판에 서느냐만 달라졌다.
 * 그래서 아래 도우미들이 「그 판을 연다」를 한 자리에 모은다.
 */
const openPanel = (name: string) => fireEvent.click(screen.getByRole("tab", { name }));

const addStep = (text: string) => {
  openPanel("체크리스트");
  const steps = screen.getByLabelText("시작 단계");
  fireEvent.change(within(steps).getByLabelText("추가할 단계"), { target: { value: text } });
  fireEvent.click(within(steps).getByRole("button", { name: "단계 추가" }));
};

/* WORK-003 정정: 「참고 업무」 표는 **만들기 창에 있다** — DB 의 `task_references` 관계와
   `reference_task_ids` 계약이 그대로 살아 있으므로, 만들 때 고를 수 있어야 한다. 고르는 자리는
   「업무 연결」 판이고 선행 업무와 같은 체크박스 표를 쓴다(업무명·프로젝트·담당자). */

describe("만들 수 있는 것이 한 가지뿐일 때 (D10)", () => {
  afterEach(cleanup);

  it("요청만 가능하면 토글 없이 「업무 요청」으로 연다 — 한 칸짜리 세그먼트를 두지 않는다", () => {
    render(
      <CreateWorkModal
        assigneeCandidates={[jiho]}
        canCreateRequest
        canCreateTask={false}
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onError={vi.fn()}
        ownerName="민아"
      />,
    );
    expect(screen.getByRole("dialog", { name: "새 업무 요청" })).toBeTruthy();
    expect(screen.queryByRole("tablist", { name: "생성 유형" })).toBeNull();
    // 왼쪽 세로 탭은 «어느 필드를 보나» 이고, 갈래 토글과 다른 축이다 — 갈래 쪽만 없어야 한다.
    expect(screen.queryByRole("tab", { name: "내 업무" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "요청 업무" })).toBeNull();
    // v2: 보내는 것만으로 담당이 서지 않는다 — 상대가 수락해야 그 사람의 업무가 된다 (V-9·V-10)
    // 최종 발주 7: 머리의 갈래 설명 문구는 걷었다 — 무엇을 만드는지는 모달 이름과 제출 단추가 말한다.
    expect(screen.queryByText(/수락 전에는 담당이 서지 않습니다/)).toBeNull();
    expect(screen.getByRole("button", { name: "업무 요청 보내기" })).toBeTruthy();
  });

  it("업무만 가능하면 「업무 추가」로 연다", () => {
    render(
      <CreateWorkModal
        assigneeCandidates={[]}
        canCreateRequest={false}
        canCreateTask
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onError={vi.fn()}
        ownerName="민아"
      />,
    );
    expect(screen.getByRole("dialog", { name: "새 업무 추가" })).toBeTruthy();
    expect(screen.queryByRole("tablist", { name: "생성 유형" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "요청 업무" })).toBeNull();
  });

  it("둘 다 가능하면 지금 그대로 — 이름은 「새 업무 추가」이고 토글이 선다", () => {
    renderDrawer();
    expect(screen.getByRole("dialog", { name: "새 업무 추가" })).toBeTruthy();
    const toggle = within(screen.getByRole("tablist", { name: "생성 유형" }));
    expect(toggle.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["내 업무", "요청 업무"]);
  });
});

describe("writing down the first steps with the work", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("sends the steps a person listed, in their order, with a new task", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    renderDrawer();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "단계까지 아는 업무" } });
    addStep("자료 모으기");
    addStep("초안 쓰기");
    // What was typed is visible before anything is sent, and can be taken back out.
    const steps = screen.getByLabelText("시작 단계");
    expect(within(steps).getAllByRole("listitem").map((row) => row.textContent)).toEqual([
      expect.stringContaining("자료 모으기"),
      expect.stringContaining("초안 쓰기"),
    ]);
    addStep("잘못 적은 단계");
    fireEvent.click(within(steps).getByRole("button", { name: "잘못 적은 단계 빼기" }));

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.checklist).toEqual(["자료 모으기", "초안 쓰기"]);
  });

  it("sends them with a request too, so the steps survive into the accepted work", async () => {
    vi.mocked(api.createWorkRequest).mockResolvedValue({ title: "부탁한 업무" } as never);
    renderDrawer();
    fireEvent.click(screen.getByRole("tab", { name: "요청 업무" }));
    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "부탁한 업무" } });
    addStep("현황 파악");

    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));
    await waitFor(() => expect(api.createWorkRequest).toHaveBeenCalled());
    expect(vi.mocked(api.createWorkRequest).mock.calls[0][2]?.checklist).toEqual(["현황 파악"]);
  });

  /**
   * WORK-003 정정 — **참고 업무는 만들기 창에서 고른다** (DB `task_references` · `reference_task_ids`).
   *
   * 한때 이 표를 내리고 「업무 상세에서 달라」고 했던 자리다. 관계도 계약도 그대로 살아 있었으므로
   * 만들 때 고를 수 있어야 한다 — 두 갈래 모두다.
   */
  it("두 갈래 모두 참고 업무를 체크박스로 고른다", async () => {
    vi.mocked(api.getTasks).mockResolvedValue([{ task_id: "task-0", title: "1분기 정산" }] as never);
    renderDrawer();
    openPanel("업무 연결");
    await waitFor(() => expect(api.getTasks).toHaveBeenCalled());
    expect(within(screen.getByRole("group", { name: "참고 업무" })).getByRole("table", { name: "참고 업무" })).toBeTruthy();

    fireEvent.click(within(screen.getByRole("tablist", { name: "생성 유형" })).getByRole("tab", { name: "요청 업무" }));
    openPanel("업무 연결");
    expect(screen.getByRole("table", { name: "참고 업무" })).toBeTruthy();
  });

  it("고른 참고 업무가 reference_task_ids 로 실려 나간다", async () => {
    vi.mocked(api.getTasks).mockResolvedValue([{ task_id: "task-0", title: "1분기 정산" }] as never);
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    renderDrawer();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "2분기 정산" } });
    openPanel("업무 연결");
    const table = within(await screen.findByRole("table", { name: "참고 업무" }));
    fireEvent.click(await table.findByRole("checkbox", { name: "1분기 정산" }));
    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.reference_task_ids).toEqual(["task-0"]);
  });

  it("아무것도 고르지 않으면 키를 지어내 보내지 않는다", async () => {
    vi.mocked(api.getTasks).mockResolvedValue([{ task_id: "task-0", title: "1분기 정산" }] as never);
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    renderDrawer();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "2분기 정산" } });
    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    // **키를 지어내 보내지 않는다** — 빈 배열도 「전부 뗀다」라는 뜻이라 아무 말도 하지 않는 편이 맞다.
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.reference_task_ids).toBeUndefined();
  });

  it("sends nothing about steps when nobody wrote any", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    renderDrawer();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "단계 없는 업무" } });
    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.checklist).toBeUndefined();
  });

  it("sends the selected project with direct creation", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    renderDrawer([{ project_id: "11111111-1111-1111-1111-111111111111", name: "AX 고도화" }]);
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "프로젝트 업무" } });
    // 최종 발주 7: 네이티브 `<select>` 가 아니라 DS 의 고르기 팝오버다 — 같은 값이 같은 키로 간다.
    openPanel("업무 연결");
    fireEvent.click(screen.getByLabelText("프로젝트"));
    fireEvent.click(await screen.findByRole("option", { name: "AX 고도화" }));

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.project_id).toBe("11111111-1111-1111-1111-111111111111");
  });
});

/* ════════════════════════════════════════════════════════════════════════════
   회의에서 여는 후속 요청 — 상태 | 요청자 두 줄이 없다 (2026-09-14 사용자 확정).

   SPEC §9-5(D40 · R-48): **요청자는 시스템(회의)이고** 누른 사람은 `promoted_by` 로 기록되고
   참조로 붙는다. 그러니 그 자리에 누른 사람 이름을 「요청자」로 내던 것은 계약과 어긋난 표시였고
   (현재 화면 18·19), 상태도 언제나 「판단 대기」라 폼이 말해 줄 것이 없다.
   **화면에서만 걷고 보내는 값은 그대로다** — 이 검사가 그 둘을 함께 본다.
   ════════════════════════════════════════════════════════════════════════════ */
describe("회의에서 여는 후속 요청 (origin=\"meeting\")", () => {
  afterEach(cleanup);

  function renderFromMeeting(onSubmitRequest = vi.fn().mockResolvedValue("보냈습니다.")) {
    render(
      <CreateWorkModal
        assigneeCandidates={[jiho]}
        canCreateRequest
        canCreateTask={false}
        ccCandidates={[{ id: "sora", display_name: "소라" } as never]}
        initial={{ title: "업무 진행과 문제 정기 공유", description: "회의에서 나온 일", dueDate: "2026-09-20", checklist: ["공유할 업무와 현재 상태를 정리한다."] }}
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onError={vi.fn()}
        onSubmitRequest={onSubmitRequest}
        origin="meeting"
        ownerName="유나 (대표)"
        size="md"
      />,
    );
    return { onSubmitRequest };
  }

  it("상태 | 요청자 두 줄이 서지 않는다 — 빈 표도 남기지 않는다", () => {
    renderFromMeeting();
    const modal = screen.getByRole("dialog", { name: "새 업무 요청" });

    expect(within(modal).queryByText("상태")).toBeNull();
    expect(within(modal).queryByText("판단 대기")).toBeNull();
    expect(within(modal).queryByText("요청자")).toBeNull();
    // 누른 사람 이름이 「요청자」로 서 있던 자리다 — 계약상 요청자는 회의다
    expect(within(modal).queryByText("유나 (대표)")).toBeNull();
    // 두 줄을 걷고 남은 빈 표를 세우지 않는다
    expect(modal.querySelector(".meta-grid")).toBeNull();
  });

  it("나머지 필드는 그대로다 — 담당 후보 · 희망 기한 · 참조자 · 시작 단계 · 요청 내용", () => {
    renderFromMeeting();
    const modal = screen.getByRole("dialog", { name: "새 업무 요청" });

    expect((within(modal).getByLabelText("요청할 업무") as HTMLInputElement).value).toBe("업무 진행과 문제 정기 공유");
    expect(within(modal).getByLabelText("담당 후보")).toBeTruthy();
    expect(within(modal).getByLabelText("희망 기한")).toBeTruthy();
    expect(within(modal).getByRole("group", { name: "참조자" })).toBeTruthy();
    // 체크리스트는 「선택」 무리의 판에 있다 — 필드는 그대로이고 자리만 옮겼다.
    expect(within(modal).getByLabelText("시작 단계")).toBeTruthy();
    expect(within(modal).getByText("공유할 업무와 현재 상태를 정리한다.")).toBeTruthy();
    expect(within(modal).getByRole("button", { name: "업무 요청 보내기" })).toBeTruthy();
  });

  it("실제로 보내는 값은 하나도 바뀌지 않는다 — 표시만 걷었다", async () => {
    const { onSubmitRequest } = renderFromMeeting();
    const modal = screen.getByRole("dialog", { name: "새 업무 요청" });

    fireEvent.click(within(modal).getByLabelText("담당 후보"));
    fireEvent.click(await screen.findByRole("option", { name: "지호 (팀장)" }));
    fireEvent.click(within(modal).getByRole("button", { name: "업무 요청 보내기" }));

    await waitFor(() => expect(onSubmitRequest).toHaveBeenCalledTimes(1));
    /* 승격 경로가 그대로다 — 출처 두 열(source_meeting_id · source_agenda_id)은 이 핸들러가
       실어 보낸다(MeetingDetailPage). 모달은 폼 값만 넘긴다. */
    expect(onSubmitRequest.mock.calls[0][0]).toMatchObject({
      assignee_id: "jiho",
      title: "업무 진행과 문제 정기 공유",
      due_date: "2026-09-20",
      checklist: ["공유할 업무와 현재 상태를 정리한다."],
    });
    // 일반 요청 경로로 새지 않는다 — 그쪽으로 가면 출처 두 열이 빠진다
    expect(api.createWorkRequest).not.toHaveBeenCalled();
  });

  /**
   * WORK-003 정정 — **회의에서 왔든 아니든 상태·요청자 카드는 없다.**
   *
   * 한때는 회의 승격에서만 그 두 줄을 걷었다(§9-5 D40). 이제 **어느 요청에서도** 서지 않는다 —
   * 둘 다 생성 입력값이 아니라 서버가 정하는 값이라, 폼이 카드로 내면 고칠 수 있는 값처럼 읽힌다.
   */
  it("회의가 아닌 요청에도 상태 | 요청자가 서지 않는다 — 갈리던 것이 하나가 됐다", () => {
    render(
      <CreateWorkModal
        assigneeCandidates={[jiho]}
        canCreateRequest
        canCreateTask={false}
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onError={vi.fn()}
        ownerName="민아"
      />,
    );
    const request = screen.getByRole("dialog", { name: "새 업무 요청" });
    expect(within(request).queryByText("상태")).toBeNull();
    expect(within(request).queryByText("판단 대기")).toBeNull();
    expect(within(request).queryByText("요청자")).toBeNull();
    // 부르는 쪽이 넘긴 이름이 「요청자」로 서 있던 자리다 — 요청자는 서버가 기록한다.
    expect(within(request).queryByText("민아")).toBeNull();
    // 두 줄을 걷고 남은 빈 표도 세우지 않는다.
    expect(request.querySelector(".meta-grid")).toBeNull();
  });
});

/*
 * 폼의 «읽히는가 / 누를 수 있는가» (2026-09-14 사용자 확정).
 * 색은 CSS 라 jsdom 이 재지 못한다 — 그래서 색을 베끼지 않고, **색을 그렇게 만든 마크업의 사실**만 본다:
 * 필수 별표가 라벨과 같은 줄에 서는가 · 접근 이름이 그대로인가 · 누를 수 있는 단추가 정말 활성인가.
 */
describe("요청 폼의 필수 표시와 활성 조작", () => {
  afterEach(cleanup);

  it("필수 별표가 라벨과 «같은 줄» 에 서고, 접근 이름은 그대로다", () => {
    render(
      <CreateWorkModal
        assigneeCandidates={[jiho]}
        canCreateRequest
        canCreateTask={false}
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onError={vi.fn()}
        ownerName="민아"
      />,
    );
    const modal = screen.getByRole("dialog", { name: "새 업무 요청" });

    /* 별표가 라벨 «밖» 이면서 같은 줄에 서야 한다 — 안으로 넣으면 접근 이름이 「요청할 업무 *」가 되고,
       줄을 안 세우면 별표가 다음 줄로 내려간다(현재 화면 27). */
    const input = within(modal).getByLabelText("요청할 업무") as HTMLInputElement;
    const label = modal.querySelector(`label[for="${input.id}"]`) as HTMLElement;
    expect(label.textContent).toBe("요청할 업무");
    const row = label.parentElement as HTMLElement;
    expect(row.className).toContain("scax-field__label-row");
    expect(row.querySelector(".danger-text")?.textContent).toBe("*");
    // 별표는 눈으로만 읽는 표시다 — 필수라는 사실은 입력칸이 진다
    expect(row.querySelector(".danger-text")?.getAttribute("aria-hidden")).toBe("true");
    expect(input.getAttribute("aria-required")).toBe("true");
  });

  it("선행 업무 표의 고르는 칸은 실제로 눌린다 — 꺼진 것처럼 두지 않는다", async () => {
    vi.mocked(api.getTasks).mockResolvedValue([
      { task_id: "task-0", title: "지난 분기 보고", project_id: "p-1" },
    ] as never);
    vi.mocked(api.listProjects).mockResolvedValue([{ project_id: "p-1", name: "AX 고도화" }] as never);
    render(
      <CreateWorkModal
        assigneeCandidates={[jiho]}
        canCreateRequest
        canCreateTask={false}
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onError={vi.fn()}
        ownerName="민아"
      />,
    );
    const modal = screen.getByRole("dialog", { name: "새 업무 요청" });

    fireEvent.click(within(modal).getByRole("tab", { name: "업무 연결" }));
    fireEvent.click(within(modal).getByLabelText("프로젝트"));
    fireEvent.click(await screen.findByRole("option", { name: "AX 고도화" }));

    const table = await within(modal).findByRole("table", { name: "선행 업무" });
    const pick = (await within(table).findByRole("checkbox", { name: "지난 분기 보고" })) as HTMLInputElement;
    // 「회색이면 disabled」가 성립하려면 «활성인 것은 disabled 가 아니어야» 한다
    expect(pick.disabled).toBe(false);
    fireEvent.click(pick);
    // 고른 것은 후보에서 빠지고 칩으로 선다 (SPEC-001 U-13)
    expect(within(table).queryByRole("checkbox", { name: "지난 분기 보고" })).toBeNull();
    expect(within(modal).getByRole("button", { name: /지난 분기 보고/ })).toBeTruthy();
  });
});

/* ════════════════════════════════════════════════════════════════════════════
   최종 발주 3 — **업무 갈래에는 담당자 칸이 없다.**

   「업무」는 내가 할 일을 만드는 자리이고, 남에게 맡기는 길은 머리의 「요청」 토글이다. 그래서 기본
   정보에 남는 것은 제목·시작일·마감일·내용 넷이고, 담당은 «현재 사용자» 로 고정된다 — 보내는 명령은
   지금까지와 같은 `POST /api/tasks`(본인 갈래) 그대로다 (SPEC-001 U-6-a).
   ════════════════════════════════════════════════════════════════════════════ */
describe("생성 창의 갈래 — 업무는 내 업무다", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  const sora = { id: "sora", display_name: "소라 (기획)" } as never;

  function renderCreate(props: Record<string, unknown> = {}) {
    const onCreated = vi.fn();
    const onError = vi.fn();
    render(
      <CreateWorkModal
        assignCandidates={[]}
        assigneeCandidates={[]}
        canCreateRequest
        canCreateTask
        onClose={vi.fn()}
        onCreated={onCreated}
        onError={onError}
        ownerName="민아"
        {...props}
      />,
    );
    return { onCreated, onError };
  }

  it("업무 갈래에는 담당자 칸이 없다 — 보낼 사람이 있어도 마찬가지다", () => {
    renderCreate({ assignCandidates: [jiho], assigneeCandidates: [sora] });
    expect(screen.queryByLabelText("담당자")).toBeNull();
    // 남에게 맡기는 길은 사라지지 않았다 — 머리의 갈래 토글이 그 자리다.
    expect(within(screen.getByRole("tablist", { name: "생성 유형" })).getByRole("tab", { name: "요청 업무" })).toBeTruthy();
  });

  it("기본 정보 판에 남는 것은 제목·시작일·마감일·업무 내용 넷이다", () => {
    renderCreate();
    expect(screen.getByLabelText("업무 제목")).toBeTruthy();
    expect(screen.getByRole("button", { name: "시작일 달력 열기" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "마감일 달력 열기" })).toBeTruthy();
    expect(screen.getByLabelText("업무 내용")).toBeTruthy();
  });

  it("생성 창에 승인자 필드가 없다 — 화면 라벨은 「결재자」 하나다", () => {
    renderCreate({ assignCandidates: [jiho], assigneeCandidates: [sora] });
    expect(screen.queryByLabelText("승인자")).toBeNull();
    expect(screen.queryByText("승인자")).toBeNull();
  });

  it("본인 업무에는 assignee_id 를 싣지 않고 멱등 키만 간다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    const { onCreated } = renderCreate({ assigneeCandidates: [sora] });
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "내 업무" } });

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    // 담당을 묻지 않는다 — **서버가 현재 사용자를 담당자로 기록한다** (SPEC-001 U-6-a).
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.assignee_id).toBeUndefined();
    expect(vi.mocked(api.createDirectTask).mock.calls[0][2]).toEqual(expect.any(String));
    expect(api.assignTask).not.toHaveBeenCalled();
    expect(onCreated).toHaveBeenCalledWith("'내 업무' 업무를 만들었습니다.", { assignedToOther: false });
  });

  it("실패한 제출을 그대로 다시 누르면 «같은 키» 다 — 두 건이 되지 않는다", async () => {
    vi.mocked(api.createDirectTask).mockRejectedValueOnce(new Error("네트워크 오류"));
    renderCreate({ assigneeCandidates: [sora] });
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "다시 보낼 업무" } });

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalledTimes(1));

    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalledTimes(2));

    const [first, second] = vi.mocked(api.createDirectTask).mock.calls;
    expect(second[2]).toBe(first[2]);
  });

  it("쓴 내용을 고쳐 다시 보내면 그것은 새 의도다 — «새 키» 로 간다", async () => {
    vi.mocked(api.createDirectTask).mockRejectedValueOnce(new Error("네트워크 오류"));
    renderCreate({ assigneeCandidates: [sora] });
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "처음 제목" } });
    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalledTimes(1));

    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "고친 제목" } });
    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalledTimes(2));

    const [first, second] = vi.mocked(api.createDirectTask).mock.calls;
    expect(second[2]).not.toBe(first[2]);
  });

  it("제출 단추를 연타해도 한 번만 나간다 — 화면에서도 두 건이 만들어지지 않는다", async () => {
    let settle: (value: unknown) => void = () => {};
    vi.mocked(api.createDirectTask).mockImplementation(() => new Promise((resolve) => { settle = resolve; }) as never);
    renderCreate({ assigneeCandidates: [sora] });
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "한 번만 생길 업무" } });

    const submit = screen.getByRole("button", { name: "업무 추가" });
    fireEvent.click(submit);
    fireEvent.click(submit);
    fireEvent.click(submit);

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "만드는 중…" }).hasAttribute("disabled")).toBe(true);
    settle({ task_id: "task-1" });
  });

  it("업무 갈래 어디에도 「수락」 대기 문구가 남아 있지 않다", () => {
    renderCreate({ assignCandidates: [jiho], assigneeCandidates: [sora] });
    const modal = screen.getByRole("dialog", { name: "새 업무 추가" });
    expect(modal.textContent).not.toMatch(/수락 대기|수락하면|수락해야/);
  });
});

/* ════════════════════════════════════════════════════════════════════════════
   시작일과 마감일 — 업무 갈래의 두 날짜.

   본인 갈래는 서버가 `start_date` 를 받는다. 두 날짜의 앞뒤가 뒤집히면 보내기 전에 화면이 먼저
   말한다 — 서버가 거절할 값을 왕복시키지 않는다.
   ════════════════════════════════════════════════════════════════════════════ */
describe("업무 갈래의 시작일과 마감일", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  function renderCreate() {
    const onError = vi.fn();
    render(
      <CreateWorkModal
        assignCandidates={[]}
        assigneeCandidates={[]}
        canCreateRequest
        canCreateTask
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onError={onError}
        ownerName="민아"
      />,
    );
    return { onError };
  }

  /* 날짜 칸은 앱의 `DatePicker` 팝오버 하나를 쓴다(DS-17) — 칸을 열고 격자에서 그 날을 누른다. */
  const pickDate = (label: string, iso: string) => {
    fireEvent.click(screen.getByRole("button", { name: `${label} 달력 열기` }));
    fireEvent.click(screen.getByRole("group", { name: label }).querySelector(`[data-date="${iso}"]`) as HTMLElement);
  };

  it("적어 둔 시작일을 그대로 실어 보낸다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    renderCreate();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "내 업무" } });
    pickDate("시작일", "2026-09-20");

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.start_date).toBe("2026-09-20");
  });

  it("시작일이 마감일보다 늦으면 보내기 전에 막는다", async () => {
    const { onError } = renderCreate();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "뒤집힌 업무" } });
    pickDate("시작일", "2026-10-05");
    pickDate("마감일", "2026-09-18");

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    await waitFor(() => expect(onError).toHaveBeenCalledWith("시작일은 기한보다 늦을 수 없습니다."));
    expect(api.createDirectTask).not.toHaveBeenCalled();
  });
});

/* ════════════════════════════════════════════════════════════════════════════
   최종 프레임 — 「업무 연결」 판의 **상위와 선행은 같은 업무일 수 없다.**

   「무엇 아래인가」(상위)와 「무엇 다음인가」(선행)는 다른 관계라, 같은 업무가 둘 다이면 뜻이 서지
   않는다. 예전에는 **여는 쪽이 준 상위**(`initial.parentTaskId`)만 선행 후보에서 뺐고, 창 안에서
   고른 상위는 그대로 후보에 남아 있었다 — 골라 놓고 서버가 거절하기를 기다리는 모양이었다.
   ════════════════════════════════════════════════════════════════════════════ */
describe("상위 업무와 선행 업무는 같은 업무일 수 없다", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  const rows = [
    { task_id: "task-a", title: "지난 분기 보고", project_id: "p-1" },
    { task_id: "task-b", title: "정산 초안", project_id: "p-1" },
  ];

  function renderLinks() {
    vi.mocked(api.getTasks).mockResolvedValue(rows as never);
    vi.mocked(api.listProjects).mockResolvedValue([{ project_id: "p-1", name: "AX 고도화" }] as never);
    const onError = vi.fn();
    render(
      <CreateWorkModal
        assigneeCandidates={[jiho]}
        canCreateRequest
        canCreateTask
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onError={onError}
        ownerName="민아"
      />,
    );
    return { onError };
  }

  const chooseProject = async () => {
    openPanel("업무 연결");
    fireEvent.click(await screen.findByLabelText("프로젝트"));
    fireEvent.click(await screen.findByRole("option", { name: "AX 고도화" }));
  };

  it("상위로 고른 업무는 선행 후보에서 빠진다 — 창 안에서 고른 것도 마찬가지다", async () => {
    renderLinks();
    await chooseProject();

    fireEvent.click(screen.getByLabelText("상위 업무"));
    fireEvent.click(await screen.findByRole("option", { name: /지난 분기 보고/ }));

    const table = within(await screen.findByRole("table", { name: "선행 업무" }));
    expect(table.queryByRole("checkbox", { name: "지난 분기 보고" })).toBeNull();
    // 다른 업무는 그대로 고를 수 있다 — 통째로 막는 것이 아니다
    expect(table.getByRole("checkbox", { name: "정산 초안" })).toBeTruthy();
  });

  it("선행으로 고른 업무는 상위 후보에서 빠진다 — 고른 것을 조용히 옮기지 않는다", async () => {
    renderLinks();
    await chooseProject();

    const table = within(await screen.findByRole("table", { name: "선행 업무" }));
    fireEvent.click(await table.findByRole("checkbox", { name: "지난 분기 보고" }));

    fireEvent.click(screen.getByLabelText("상위 업무"));
    // 목록이 뜬 것을 먼저 확인한다 — 안 뜬 목록에서 「없다」를 읽으면 아무것도 못 박은 것이 아니다
    expect(await screen.findByRole("option", { name: /정산 초안/ })).toBeTruthy();
    expect(screen.queryByRole("option", { name: /지난 분기 보고/ })).toBeNull();
  });

  it("선행에서 빼면 상위 후보에 다시 선다 — 막는 것이지 지우는 것이 아니다", async () => {
    renderLinks();
    await chooseProject();

    const table = within(await screen.findByRole("table", { name: "선행 업무" }));
    fireEvent.click(await table.findByRole("checkbox", { name: "지난 분기 보고" }));
    // 고른 것은 칩으로 서고, 칩이 빼는 길이다 (SPEC-001 U-13)
    fireEvent.click(screen.getByRole("button", { name: /지난 분기 보고/ }));

    fireEvent.click(screen.getByLabelText("상위 업무"));
    expect(await screen.findByRole("option", { name: /지난 분기 보고/ })).toBeTruthy();
  });

  /**
   * 여는 쪽이 정해 준 상위(하위 요청 · 다시 요청)는 고르기를 거치지 않는다 — 그 길로도 같은 업무가
   * 둘 다가 되지 않게 제출 직전에 한 번 더 굳힌다.
   */
  it("여는 쪽이 준 상위도 선행 후보에서 빠진다", async () => {
    vi.mocked(api.getTasks).mockResolvedValue(rows as never);
    vi.mocked(api.listProjects).mockResolvedValue([{ project_id: "p-1", name: "AX 고도화" }] as never);
    render(
      <CreateWorkModal
        assigneeCandidates={[jiho]}
        canCreateRequest
        canCreateTask={false}
        initial={{ parentTaskId: "task-a" }}
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onError={vi.fn()}
        ownerName="민아"
      />,
    );
    openPanel("업무 연결");
    fireEvent.click(await screen.findByLabelText("프로젝트"));
    fireEvent.click(await screen.findByRole("option", { name: "AX 고도화" }));

    const table = within(await screen.findByRole("table", { name: "선행 업무" }));
    expect(table.queryByRole("checkbox", { name: "지난 분기 보고" })).toBeNull();
    expect(table.getByRole("checkbox", { name: "정산 초안" })).toBeTruthy();
  });
});

/* ════════════════════════════════════════════════════════════════════════════
   요청 갈래의 날짜 순서 — **시작일이 희망 기한보다 늦을 수 없다.**

   요청 생성도 `start_date` 를 받으므로(SPEC-001 U-6-a) 뒤집힌 두 날짜를 업무 갈래에서만 막던 것은
   갈래마다 다른 규칙이 아니라 **빠뜨린 것**이었다. 서버가 거절할 값을 왕복시키지 않는다.
   ════════════════════════════════════════════════════════════════════════════ */
describe("요청 갈래의 시작일과 희망 기한", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  function renderRequest() {
    const onError = vi.fn();
    render(
      <CreateWorkModal
        assigneeCandidates={[jiho]}
        canCreateRequest
        canCreateTask={false}
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onError={onError}
        ownerName="민아"
      />,
    );
    return { onError };
  }

  const pickDate = (label: string, iso: string) => {
    fireEvent.click(screen.getByRole("button", { name: `${label} 달력 열기` }));
    fireEvent.click(screen.getByRole("group", { name: label }).querySelector(`[data-date="${iso}"]`) as HTMLElement);
  };

  it("시작일이 희망 기한보다 늦으면 보내기 전에 막는다", async () => {
    const { onError } = renderRequest();
    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "뒤집힌 요청" } });
    fireEvent.click(screen.getByLabelText("담당 후보"));
    fireEvent.click(await screen.findByRole("option", { name: "지호 (팀장)" }));
    pickDate("시작일", "2026-10-05");
    pickDate("희망 기한", "2026-09-18");

    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));

    // 「기한」이 아니라 이 갈래가 쓰는 말(희망 기한)로 말한다
    await waitFor(() => expect(onError).toHaveBeenCalledWith("시작일은 희망 기한보다 늦을 수 없습니다."));
    expect(api.createWorkRequest).not.toHaveBeenCalled();
  });

  it("앞뒤가 맞으면 그대로 나간다 — 막는 것은 뒤집힌 경우뿐이다", async () => {
    vi.mocked(api.createWorkRequest).mockResolvedValue({ title: "제대로 된 요청" } as never);
    renderRequest();
    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "제대로 된 요청" } });
    fireEvent.click(screen.getByLabelText("담당 후보"));
    fireEvent.click(await screen.findByRole("option", { name: "지호 (팀장)" }));
    pickDate("시작일", "2026-09-18");
    pickDate("희망 기한", "2026-10-05");

    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));

    await waitFor(() => expect(api.createWorkRequest).toHaveBeenCalled());
    expect(vi.mocked(api.createWorkRequest).mock.calls[0][2]?.start_date).toBe("2026-09-18");
    expect(vi.mocked(api.createWorkRequest).mock.calls[0][2]?.due_date).toBe("2026-10-05");
  });
});

/* ════════════════════════════════════════════════════════════════════════════
   후보 권한 게이트 — **업무만 만들 수 있는 사람에게서 참조자·결재자를 감추지 않는다.**

   두 값 모두 `POST /api/tasks` 본인 갈래가 받는다(`cc_member_ids` · `approver_id`). 요청 권한이
   없다는 이유로 그 칸을 접으면, 고를 수 있는 것을 권한 없어 못 고르는 것처럼 감추게 된다.
   부르는 쪽의 후보 로딩은 `MyWorkPage`·`TodayPage` 가 지고, 여기서는 **후보가 오면 칸이 선다**를 본다.
   ════════════════════════════════════════════════════════════════════════════ */
describe("업무만 만들 수 있는 사람의 참조자·결재자", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  const sora = { id: "sora", display_name: "소라 (기획)" } as never;

  it("요청 권한이 없어도 참조자와 결재자 칸이 선다", () => {
    render(
      <CreateWorkModal
        assigneeCandidates={[]}
        canCreateRequest={false}
        canCreateTask
        ccCandidates={[sora]}
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onError={vi.fn()}
        ownerName="민아"
      />,
    );
    expect(screen.getByRole("group", { name: "참조자" })).toBeTruthy();
    expect(screen.getByLabelText("결재자")).toBeTruthy();
  });

  it("고른 값이 그대로 `POST /api/tasks` 로 나간다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "t9" } as never);
    render(
      <CreateWorkModal
        assigneeCandidates={[]}
        canCreateRequest={false}
        canCreateTask
        ccCandidates={[sora, { id: "yuna", display_name: "유나" } as never]}
        onClose={vi.fn()}
        onCreated={vi.fn()}
        onError={vi.fn()}
        ownerName="민아"
      />,
    );
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "참조자를 단 업무" } });
    fireEvent.click(within(screen.getByRole("group", { name: "참조자" })).getByRole("checkbox", { name: "소라" }));
    fireEvent.click(screen.getByLabelText("결재자"));
    fireEvent.click(await screen.findByRole("option", { name: "유나" }));

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.cc_member_ids).toEqual(["sora"]);
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.approver_id).toBe("yuna");
  });
});
