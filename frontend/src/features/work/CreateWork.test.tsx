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

const addStep = (text: string) => {
  const steps = screen.getByLabelText("시작 단계");
  fireEvent.change(within(steps).getByLabelText("추가할 단계"), { target: { value: text } });
  fireEvent.click(within(steps).getByRole("button", { name: "단계 추가" }));
};

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
    expect(screen.getByRole("dialog", { name: "업무 요청" })).toBeTruthy();
    expect(screen.queryByRole("tablist", { name: "생성 유형" })).toBeNull();
    expect(screen.queryByRole("tab")).toBeNull();
    // v2: 보내는 것만으로 담당이 서지 않는다 — 상대가 수락해야 그 사람의 업무가 된다 (V-9·V-10)
    expect(screen.getByText("상대가 수락해야 그 사람의 업무가 됩니다. 수락 전에는 담당이 서지 않습니다.")).toBeTruthy();
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
    expect(screen.getByRole("dialog", { name: "업무 추가" })).toBeTruthy();
    expect(screen.queryByRole("tab")).toBeNull();
  });

  it("둘 다 가능하면 지금 그대로 — 이름은 「새 업무 추가」이고 토글이 선다", () => {
    renderDrawer();
    expect(screen.getByRole("dialog", { name: "새 업무 추가" })).toBeTruthy();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["업무", "요청"]);
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
    fireEvent.click(screen.getByRole("tab", { name: "요청" }));
    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "부탁한 업무" } });
    addStep("현황 파악");

    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));
    await waitFor(() => expect(api.createWorkRequest).toHaveBeenCalled());
    expect(vi.mocked(api.createWorkRequest).mock.calls[0][2]?.checklist).toEqual(["현황 파악"]);
  });

  it("points the new work at earlier work chosen from what this person can read", async () => {
    vi.mocked(api.getTasks).mockResolvedValue([
      { task_id: "task-0", title: "1분기 정산" },
      { task_id: "task-8", title: "다른 업무" },
    ] as never);
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    renderDrawer();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "2분기 정산" } });

    await waitFor(() => expect(api.getTasks).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole("checkbox", { name: "1분기 정산" }));
    expect(screen.getByText("1분기 정산")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.reference_task_ids).toEqual(["task-0"]);
  });

  it("sends the same pointers with a request, so they travel to the work it becomes", async () => {
    vi.mocked(api.getTasks).mockResolvedValue([{ task_id: "task-0", title: "지난 분기 보고" }] as never);
    vi.mocked(api.createWorkRequest).mockResolvedValue({ title: "이번 분기 보고" } as never);
    renderDrawer();
    fireEvent.click(screen.getByRole("tab", { name: "요청" }));
    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "이번 분기 보고" } });
    fireEvent.click(screen.getByRole("button", { name: "참고 업무 연결" }));
    fireEvent.click(await screen.findByLabelText("연결할 이전 업무"));
    fireEvent.click(screen.getByRole("option", { name: "지난 분기 보고" }));
    fireEvent.click(screen.getByRole("button", { name: "연결" }));

    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));
    await waitFor(() => expect(api.createWorkRequest).toHaveBeenCalled());
    expect(vi.mocked(api.createWorkRequest).mock.calls[0][2]?.reference_task_ids).toEqual(["task-0"]);
  });

  it("sends nothing about steps when nobody wrote any", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    renderDrawer();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "단계 없는 업무" } });
    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.checklist).toBeUndefined();
  });

  it("reuses the typed Task fields and sends the selected project with direct creation", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    const { container } = renderDrawer([{ project_id: "11111111-1111-1111-1111-111111111111", name: "AX 고도화" }]);
    expect(container.querySelector(".action-task-fields")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "프로젝트 업무" } });
    fireEvent.change(screen.getByLabelText("프로젝트"), { target: { value: "11111111-1111-1111-1111-111111111111" } });

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
    const modal = screen.getByRole("dialog", { name: "업무 요청" });

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
    const modal = screen.getByRole("dialog", { name: "업무 요청" });

    expect((within(modal).getByLabelText("요청할 업무") as HTMLInputElement).value).toBe("업무 진행과 문제 정기 공유");
    expect(within(modal).getByLabelText("담당 후보")).toBeTruthy();
    expect(within(modal).getByLabelText("희망 기한")).toBeTruthy();
    expect(within(modal).getByRole("group", { name: "참조자" })).toBeTruthy();
    expect(within(modal).getByLabelText("시작 단계")).toBeTruthy();
    expect(within(modal).getByText("공유할 업무와 현재 상태를 정리한다.")).toBeTruthy();
    expect(within(modal).getByRole("button", { name: "업무 요청 보내기" })).toBeTruthy();
  });

  it("실제로 보내는 값은 하나도 바뀌지 않는다 — 표시만 걷었다", async () => {
    const { onSubmitRequest } = renderFromMeeting();
    const modal = screen.getByRole("dialog", { name: "업무 요청" });

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

  it("회의가 아닌 요청에는 상태 | 요청자가 그대로 선다 — 회의에서만 걷는다", () => {
    /* 이 표는 «요청» 갈래의 것이다 — 업무 갈래는 `TaskDraftFields` 가 따로 그려 상태 줄 자체가 없다.
       그래서 견줄 짝은 「회의가 아닌 요청」이다. */
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
    const request = screen.getByRole("dialog", { name: "업무 요청" });
    expect(within(request).getByText("상태")).toBeTruthy();
    // v2: 보낸 요청은 `pending` 으로 선다 — 판단을 기다린다 (SPEC-003 §4 State)
    expect(within(request).getByText("판단 대기")).toBeTruthy();
    expect(within(request).queryByText("즉시 배정됨")).toBeNull();
    expect(within(request).getByText("요청자")).toBeTruthy();
    expect(within(request).getByText("민아")).toBeTruthy();
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
    const modal = screen.getByRole("dialog", { name: "업무 요청" });

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

  it("[참고 업무 연결]은 실제로 누를 수 있는 단추다 — 꺼진 것처럼 두지 않는다", async () => {
    vi.mocked(api.getTasks).mockResolvedValue([] as never);
    // 「참고 업무」 칸은 요청 갈래의 것이다 — 요청만 되는 자리로 바로 연다
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
    const modal = screen.getByRole("dialog", { name: "업무 요청" });

    const link = within(modal).getByRole("button", { name: "참고 업무 연결" });
    // 「회색이면 disabled」가 성립하려면 «활성인 것은 disabled 가 아니어야» 한다
    expect(link.hasAttribute("disabled")).toBe(false);
    // 글자만 있는 결이 아니라 단추로 보이는 결이다 (DS outlined-neutral)
    expect(link.className).toContain("scax-button--outlined-neutral");

    // variant 를 바꿔도 하던 일은 그대로다
    fireEvent.click(link);
    expect(await within(modal).findByRole("button", { name: "연결 취소" })).toBeTruthy();
  });
});

/* ════════════════════════════════════════════════════════════════════════════
   W1 — 생성 창의 담당 필드와 즉시 배정 (WORK-001 Phase 7).

   한 번의 생성 명령으로 업무가 실재한다. 담당이 남이면 **상대의 수락 없이** 바로 그 사람의
   업무가 된다. 후보는 envelope 이 허용한 경로의 목록만 합치고, 같은 사람이 둘 다에 있으면
   기존 관리자 배정 경로를 우선한다 — 재시도 도중 endpoint 가 바뀌지 않는다.
   ════════════════════════════════════════════════════════════════════════════ */
describe("W1 생성 창 — 담당과 즉시 배정", () => {
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

  const pickOwner = (value: string) => fireEvent.change(screen.getByLabelText("담당자"), { target: { value } });

  it("담당 기본값은 나이고, 후보는 배정 후보와 수신 후보를 겹치지 않게 합친다", () => {
    renderCreate({ assignCandidates: [jiho], assigneeCandidates: [jiho, sora] });
    const owner = screen.getByLabelText("담당자") as HTMLSelectElement;
    expect(owner.value).toBe("me");
    // 같은 사람(jiho)이 두 목록에 있어도 한 번만 선다
    expect(Array.from(owner.options).map((option) => option.value)).toEqual(["", "me", "jiho", "sora"]);
    expect(Array.from(owner.options).map((option) => option.textContent)).toEqual(["선택 안 함", "민아 (나)", "지호 (팀장)", "소라 (기획)"]);
  });

  it("보낼 수 있는 사람이 하나도 없으면 담당 줄 자체가 서지 않는다", () => {
    renderCreate();
    expect(screen.queryByLabelText("담당자")).toBeNull();
  });

  it("생성 창에 승인자 필드가 없다 — W2 에서 완료 경로와 함께 연다", () => {
    renderCreate({ assignCandidates: [jiho], assigneeCandidates: [sora] });
    expect(screen.queryByLabelText("승인자")).toBeNull();
    expect(screen.queryByText("승인자")).toBeNull();
  });

  it("수신 후보에게 보내면 POST /api/tasks 에 assignee_id 를 실어 보내고, 누구의 업무가 되었는지 말한다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    const { onCreated } = renderCreate({ assigneeCandidates: [sora] });
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "전망치 정리" } });
    pickOwner("sora");
    // 타인 지정이라는 사실을 문구가 먼저 말한다
    expect(screen.getByText("소라에게 요청을 보냅니다. 상대가 수락해야 그 사람의 업무가 됩니다.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "업무 배정" }));

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.assignee_id).toBe("sora");
    expect(vi.mocked(api.createDirectTask).mock.calls[0][2]).toEqual(expect.any(String));
    // 배정 경로로 새지 않는다 — 이 사람은 관리자 배정 후보가 아니다
    expect(api.assignTask).not.toHaveBeenCalled();
    expect(onCreated).toHaveBeenCalledWith("'전망치 정리' 업무를 소라에게 보냈습니다. 상대가 수락하면 그 사람의 업무가 됩니다.", { assignedToOther: true });
  });

  it("두 목록에 다 있는 사람은 기존 관리자 배정 경로로 간다 — overlap 에서 managed 가 우선이다", async () => {
    vi.mocked(api.assignTask).mockResolvedValue({ assignment_id: "as-1" } as never);
    renderCreate({ assignCandidates: [jiho], assigneeCandidates: [jiho] });
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "실적 정리" } });
    pickOwner("jiho");

    fireEvent.click(screen.getByRole("button", { name: "업무 배정" }));

    await waitFor(() => expect(api.assignTask).toHaveBeenCalled());
    expect(vi.mocked(api.assignTask).mock.calls[0][1]).toBe("jiho");
    expect(vi.mocked(api.assignTask).mock.calls[0][3]).toEqual(expect.any(String));
    expect(api.createDirectTask).not.toHaveBeenCalled();
  });

  it("본인 업무에는 assignee_id 를 싣지 않고 멱등 키만 간다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    const { onCreated } = renderCreate({ assigneeCandidates: [sora] });
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "내 업무" } });

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.assignee_id).toBeUndefined();
    expect(vi.mocked(api.createDirectTask).mock.calls[0][2]).toEqual(expect.any(String));
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

  it("담당을 바꿔 다시 보내면 경로도 키도 새로 잡는다 — 재시도 도중 endpoint 가 바뀌지 않는다", async () => {
    vi.mocked(api.createDirectTask).mockRejectedValueOnce(new Error("네트워크 오류"));
    vi.mocked(api.assignTask).mockResolvedValue({ assignment_id: "as-1" } as never);
    renderCreate({ assignCandidates: [jiho], assigneeCandidates: [sora] });
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "옮길 업무" } });
    pickOwner("sora");
    fireEvent.click(screen.getByRole("button", { name: "업무 배정" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalledTimes(1));

    pickOwner("jiho");
    fireEvent.click(screen.getByRole("button", { name: "업무 배정" }));
    await waitFor(() => expect(api.assignTask).toHaveBeenCalledTimes(1));
    // 실패한 수평 경로로 다시 가지 않는다 — 다른 대상은 다른 명령이다
    expect(api.createDirectTask).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.assignTask).mock.calls[0][3]).not.toBe(vi.mocked(api.createDirectTask).mock.calls[0][2]);
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

  it("화면 어디에도 「수락」 대기 문구가 남아 있지 않다", () => {
    renderCreate({ assignCandidates: [jiho], assigneeCandidates: [sora] });
    pickOwner("jiho");
    const modal = screen.getByRole("dialog", { name: "새 업무 추가" });
    expect(modal.textContent).not.toMatch(/수락 대기|수락하면|수락해야/);
  });
});

/* ════════════════════════════════════════════════════════════════════════════
   F-1 — 수평 생성이 받지 않는 「시작일」 (리뷰 리포트 2026-09-16).

   서버는 담당을 지정한 생성에서 `start_date`·`parent_task_id`·`project_id` 를 **조용히 버리지 않고**
   422 로 거절한다(`creation_commands.py` `_refuse_unsupported_horizontal_fields`). 화면이 그 사실을
   모른 채 시작일을 묻고 실어 보내면 W1 의 대표 동선(일반 구성원 → 동료에게 보내기)이 통째로 막힌다.
   그래서 **묻지 않고 보내지 않는다.** 본인·관리자 배정이 지금까지 받던 것은 그대로 받는다.
   ════════════════════════════════════════════════════════════════════════════ */
describe("F-1 수평 생성과 시작일", () => {
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

  const pickOwner = (value: string) => fireEvent.change(screen.getByLabelText("담당자"), { target: { value } });
  /* 날짜 칸은 앱의 `DatePicker` 팝오버 하나를 쓴다(DS-17) — 칸을 열고 격자에서 그 날을 누른다.
     9월 격자는 8/30~10/10 이라 아래 날짜는 모두 한 화면에 선다. */
  const pickDate = (label: string, iso: string) => {
    fireEvent.click(screen.getByRole("button", { name: `${label} 달력 열기` }));
    fireEvent.click(screen.getByRole("group", { name: label }).querySelector(`[data-date="${iso}"]`) as HTMLElement);
  };
  const startDateField = () => screen.queryByRole("button", { name: "시작일 달력 열기" });
  const typeStartDate = (value: string) => pickDate("시작일", value);

  it("시작일을 먼저 적고 수신 후보를 고르면 줄이 걷히고 payload 에도 start_date 가 없다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    const { onCreated, onError } = renderCreate({ assigneeCandidates: [sora] });
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "전망치 정리" } });
    // 본인 업무일 때는 지금까지대로 묻는다
    typeStartDate("2026-09-20");

    pickOwner("sora");

    // 묻지 않는다 — 서버가 받지 않는 값이라 줄 자체를 세우지 않는다
    expect(startDateField()).toBeNull();
    // 적어 둔 것이 조용히 사라지지 않게 말해 준다
    expect(screen.getByText("적어 둔 시작일은 보내지 않습니다 — 언제 시작할지는 담당자가 정합니다.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "업무 배정" }));

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    const [, extra] = vi.mocked(api.createDirectTask).mock.calls[0];
    expect(extra?.assignee_id).toBe("sora");
    // 422 를 부르는 값이 실려 나가지 않는다 — 키 자체가 없다
    expect(extra && "start_date" in extra).toBe(false);
    expect(extra?.start_date).toBeUndefined();
    // 그래서 생성이 성공한다
    expect(onError).not.toHaveBeenCalledWith(expect.stringContaining("쓸 수 없는 항목"));
    expect(onCreated).toHaveBeenCalledWith("'전망치 정리' 업무를 소라에게 보냈습니다. 상대가 수락하면 그 사람의 업무가 됩니다.", { assignedToOther: true });
  });

  it("본인 업무는 지금까지대로 시작일을 묻고 실어 보낸다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    renderCreate({ assigneeCandidates: [sora] });
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "내 업무" } });
    typeStartDate("2026-09-20");

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.start_date).toBe("2026-09-20");
  });

  it("관리자 배정도 지금까지대로 시작일을 묻고 실어 보낸다 — 걷는 것은 수평 경로뿐이다", async () => {
    vi.mocked(api.assignTask).mockResolvedValue({ assignment_id: "as-1" } as never);
    renderCreate({ assignCandidates: [jiho] });
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "실적 정리" } });
    pickOwner("jiho");

    // 관리자 배정은 서버가 받는 값이라 줄이 그대로 선다
    expect(startDateField()).toBeTruthy();
    typeStartDate("2026-09-21");
    expect(screen.queryByText("적어 둔 시작일은 보내지 않습니다 — 언제 시작할지는 담당자가 정합니다.")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "업무 배정" }));

    await waitFor(() => expect(api.assignTask).toHaveBeenCalled());
    expect(vi.mocked(api.assignTask).mock.calls[0][2]?.start_date).toBe("2026-09-21");
  });

  it("담당을 나로 되돌리면 적어 둔 시작일이 그대로 다시 서고 다시 실려 간다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    renderCreate({ assigneeCandidates: [sora] });
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "오가는 업무" } });
    typeStartDate("2026-09-20");

    pickOwner("sora");
    expect(startDateField()).toBeNull();
    pickOwner("me");

    // 값을 지우지 않았다 — 고쳐 쓴 것을 돌려받는다 (AX 카드 결이라 화면 구분자는 점이다)
    expect(startDateField()?.textContent).toBe("2026.09.20");
    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.start_date).toBe("2026-09-20");
  });

  it("숨긴 시작일이 기한보다 늦어도 수평 제출을 막지 않는다 — 안 보내는 값이 사람을 세우지 않는다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    const { onError } = renderCreate({ assigneeCandidates: [sora] });
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "기한이 앞선 업무" } });
    typeStartDate("2026-10-05");
    pickDate("기한", "2026-09-18");

    pickOwner("sora");
    fireEvent.click(screen.getByRole("button", { name: "업무 배정" }));

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalled());
    expect(onError).not.toHaveBeenCalledWith("시작일은 기한보다 늦을 수 없습니다.");
    expect(vi.mocked(api.createDirectTask).mock.calls[0][1]?.due_date).toBe("2026-09-18");
  });

  it("숨긴 시작일은 새 의도 지문에 섞이지 않는다 — 실패 재시도가 같은 키로 간다", async () => {
    vi.mocked(api.createDirectTask).mockRejectedValueOnce(new Error("네트워크 오류"));
    renderCreate({ assigneeCandidates: [sora] });
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "다시 보낼 업무" } });
    typeStartDate("2026-09-20");
    pickOwner("sora");

    fireEvent.click(screen.getByRole("button", { name: "업무 배정" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalledTimes(1));

    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-1" } as never);
    fireEvent.click(screen.getByRole("button", { name: "업무 배정" }));
    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalledTimes(2));

    const [first, second] = vi.mocked(api.createDirectTask).mock.calls;
    expect(second[2]).toBe(first[2]);
    expect(second[1] && "start_date" in second[1]).toBe(false);
  });

  it("제출 중에는 단추가 잠기고 연타해도 한 번만 나간다 — 수평 경로에서도 같다", async () => {
    let settle: (value: unknown) => void = () => {};
    vi.mocked(api.createDirectTask).mockImplementation(() => new Promise((resolve) => { settle = resolve; }) as never);
    renderCreate({ assigneeCandidates: [sora] });
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "한 번만 생길 업무" } });
    typeStartDate("2026-09-20");
    pickOwner("sora");

    const submit = screen.getByRole("button", { name: "업무 배정" });
    fireEvent.click(submit);
    fireEvent.click(submit);

    await waitFor(() => expect(api.createDirectTask).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "만드는 중…" }).hasAttribute("disabled")).toBe(true);
    settle({ task_id: "task-1" });
  });
});
