import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", () => ({
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
    expect(screen.getByText("동료가 수락해야 그 사람의 업무가 됩니다. 희망 기한을 함께 보낼 수 있습니다.")).toBeTruthy();
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
    expect(within(request).getByText("판단 대기")).toBeTruthy();
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
