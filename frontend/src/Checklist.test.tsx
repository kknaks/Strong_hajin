import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DirectTask } from "./viewModels";

vi.mock("./api", () => ({
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
  getTaskAssignmentCandidates: vi.fn(),
  reassignTask: vi.fn(),
  taskMaterialContentUrl: (taskId: string, materialId: string) => `/api/tasks/${taskId}/materials/${materialId}/content`,
  addWorkRequestComment: vi.fn(),
  getWorkRequestTimeline: vi.fn(),
  uploadCommentAttachment: vi.fn(),
  uploadRequestEvidence: vi.fn(),
  requestAttachmentUrl: () => "",
  resubmitWorkRequest: vi.fn(),
  decideWorkRequest: vi.fn(),
  negotiateWorkRequest: vi.fn(),
}));

import * as api from "./api";
import { TaskDetailDrawer } from "./WorkModals";

const task: DirectTask = { task_id: "task-1", title: "분기 보고 준비", state: "open", version: 1, block_reason: null };

const step = (id: string, text: string, position: number, done = false, version = 1) => ({
  item_id: id,
  text,
  position,
  done,
  state: "active",
  version,
  created_by: "mina",
  completed_by: done ? "mina" : null,
  completed_at: done ? "2026-09-04T01:00:00Z" : null,
});

function renderDrawer(items: ReturnType<typeof step>[], canManage = true) {
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTask).mockResolvedValue({ ...task, checklist: items } as never);
  const onError = vi.fn();
  const onChanged = vi.fn();
  const onUpdate = vi.fn();
  const view = render(
    <TaskDetailDrawer
      busy={false}
      canManage={canManage}
      onChanged={onChanged}
      onClose={vi.fn()}
      onError={onError}
      onNotice={vi.fn()}
      onTransition={vi.fn()}
      onUpdate={onUpdate}
      ownerName="민아"
      task={task}
    />,
  );
  const rerenderWith = (next: DirectTask) =>
    view.rerender(
      <TaskDetailDrawer
        busy={false}
        canManage={canManage}
        onChanged={onChanged}
        onClose={vi.fn()}
        onError={onError}
        onNotice={vi.fn()}
        onTransition={vi.fn()}
        onUpdate={onUpdate}
        ownerName="민아"
        task={next}
      />,
    );
  return { onError, onChanged, onUpdate, rerenderWith };
}

describe("task checklist", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("lists the steps in order with progress, and marks the finished ones", async () => {
    renderDrawer([step("i1", "자료 모으기", 1, true), step("i2", "초안 쓰기", 2), step("i3", "검토 요청", 3)]);
    const section = await screen.findByLabelText("체크리스트");
    await waitFor(() => expect(within(section).getByText("1/3")).toBeTruthy());
    expect(Array.from(section.querySelectorAll(".checklist-item span")).map((node) => node.textContent)).toEqual([
      "자료 모으기",
      "초안 쓰기",
      "검토 요청",
    ]);
    expect((section.querySelector('[data-item-id="i1"]') as HTMLElement).className).toContain("done");
    expect((within(section).getByRole("checkbox", { name: "자료 모으기" }) as HTMLInputElement).checked).toBe(true);
    expect((within(section).getByRole("checkbox", { name: "초안 쓰기" }) as HTMLInputElement).checked).toBe(false);
  });

  it("adds a step on Enter without double-submitting, and clears the field", async () => {
    vi.mocked(api.addChecklistItem).mockResolvedValue(step("i9", "제출하기", 1) as never);
    renderDrawer([]);
    const section = await screen.findByLabelText("체크리스트");
    expect(within(section).getByText(/아직 단계가 없습니다/)).toBeTruthy();
    const field = within(section).getByLabelText("체크리스트 단계");
    fireEvent.change(field, { target: { value: "  제출하기  " } });

    fireEvent.keyDown(field, { key: "Enter" });
    fireEvent.keyDown(field, { key: "Enter", repeat: true });
    fireEvent.click(within(section).getByRole("button", { name: "추가" }));

    expect(api.addChecklistItem).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.addChecklistItem).mock.calls[0]).toEqual(["task-1", "제출하기"]);
    await waitFor(() => expect((field as HTMLInputElement).value).toBe(""));
    expect(within(section).getByText("제출하기")).toBeTruthy();
    await waitFor(() => expect(within(section).getByText("0/1")).toBeTruthy());
  });

  it("does not wipe what the user typed while the previous step was still being saved", async () => {
    let release: ((value: unknown) => void) | null = null;
    vi.mocked(api.addChecklistItem).mockImplementation(() => new Promise((resolve) => {
      release = () => resolve(step("i1", "자료 모으기", 1) as never);
    }));
    renderDrawer([]);
    const section = await screen.findByLabelText("체크리스트");
    const field = within(section).getByLabelText("체크리스트 단계") as HTMLInputElement;

    fireEvent.change(field, { target: { value: "자료 모으기" } });
    fireEvent.keyDown(field, { key: "Enter" });
    await waitFor(() => expect(release).not.toBeNull());
    // The next step is typed before the first one comes back.
    fireEvent.change(field, { target: { value: "초안 쓰기" } });
    await act(async () => {
      release?.(undefined);
    });

    await waitFor(() => expect(within(section).getByText("자료 모으기")).toBeTruthy());
    expect(field.value).toBe("초안 쓰기"); // the in-progress text survived
  });

  it("checks a step off and moves the progress with the server's answer", async () => {
    vi.mocked(api.updateChecklistItem).mockResolvedValue(step("i2", "초안 쓰기", 2, true) as never);
    renderDrawer([step("i1", "자료 모으기", 1), step("i2", "초안 쓰기", 2)]);
    const section = await screen.findByLabelText("체크리스트");
    await waitFor(() => expect(within(section).getByText("0/2")).toBeTruthy());

    fireEvent.click(within(section).getByRole("checkbox", { name: "초안 쓰기" }));
    await waitFor(() => expect(api.updateChecklistItem).toHaveBeenCalledWith("task-1", "i2", { done: true, expected_version: 1 }));
    await waitFor(() => expect(within(section).getByText("1/2")).toBeTruthy());
    expect((section.querySelector('[data-item-id="i2"]') as HTMLElement).className).toContain("done");
  });

  it("removes a step and reports a failure instead of dropping it from the view", async () => {
    vi.mocked(api.removeChecklistItem).mockResolvedValueOnce(undefined as never).mockRejectedValueOnce(new Error("서버 오류"));
    const { onError } = renderDrawer([step("i1", "자료 모으기", 1), step("i2", "초안 쓰기", 2)]);
    const section = await screen.findByLabelText("체크리스트");

    fireEvent.click(within(section).getByRole("button", { name: "자료 모으기 삭제" }));
    await waitFor(() => expect(section.querySelector('[data-item-id="i1"]')).toBeNull());
    await waitFor(() => expect(within(section).getByText("0/1")).toBeTruthy());

    fireEvent.click(within(section).getByRole("button", { name: "초안 쓰기 삭제" }));
    await waitFor(() => expect(onError).toHaveBeenCalledWith(expect.stringContaining("서버 오류")));
    expect(section.querySelector('[data-item-id="i2"]')).toBeTruthy(); // still there, because the server refused
  });

  it("puts the checklist above the description so it is on the first screen, with progress in the header", async () => {
    renderDrawer([step("i1", "자료 모으기", 1, true), step("i2", "초안 쓰기", 2)]);
    const section = await screen.findByLabelText("체크리스트");
    // The count reads as a chip, not buried in the item list.
    const chip = section.querySelector(".checklist-progress") as HTMLElement;
    expect(chip.textContent).toBe("1/2");
    expect(chip.getAttribute("data-done")).toBe("1");
    expect(chip.getAttribute("data-total")).toBe("2");
    // The add control ships with the section rather than after a long description field.
    expect(within(section).getByLabelText("체크리스트 단계")).toBeTruthy();

    const description = document.getElementById("task-description-task-1") as HTMLElement;
    const order = section.compareDocumentPosition(description);
    expect(order & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy(); // checklist comes first in the document
    expect((description as HTMLTextAreaElement).rows).toBe(4); // and the description cannot grow past it
  });

  it("shows a compact cue on list rows only when the task actually has steps", async () => {
    const { ChecklistCue } = await import("./WorkViews");
    const { container, rerender } = render(<ChecklistCue progress={{ done: 1, total: 3 }} />);
    expect(container.textContent).toBe("☐ 1/3");
    rerender(<ChecklistCue progress={{ done: 3, total: 3 }} />);
    expect(container.textContent).toBe("☑ 3/3");
    expect(container.querySelector(".checklist-cue.complete")).toBeTruthy();
    rerender(<ChecklistCue progress={{ done: 0, total: 0 }} />);
    expect(container.textContent).toBe("");
    rerender(<ChecklistCue />);
    expect(container.textContent).toBe("");
  });

  it("settles the version the server moved, so the next edit is not refused as stale", async () => {
    // A checklist change is a change to the task: the server freezes a new version for it. A drawer that keeps
    // holding the version it opened with would send a stale expected_version on the very next save.
    vi.mocked(api.addChecklistItem).mockResolvedValue(step("i9", "제출하기", 1) as never);
    vi.mocked(api.updateChecklistItem).mockResolvedValue(step("i9", "제출하기", 1, true) as never);
    vi.mocked(api.removeChecklistItem).mockResolvedValue(undefined as never);
    const { onChanged } = renderDrawer([]);
    const section = await screen.findByLabelText("체크리스트");

    fireEvent.change(within(section).getByLabelText("체크리스트 단계"), { target: { value: "제출하기" } });
    fireEvent.click(within(section).getByRole("button", { name: "추가" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));

    fireEvent.click(within(section).getByRole("checkbox", { name: "제출하기" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(2));

    fireEvent.click(within(section).getByRole("button", { name: "제출하기 삭제" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(3));
  });

  it("saves with the version the server just answered, without waiting for the refresh", async () => {
    // The refresh never comes back here. The mutation's own answer is enough: an edit made right after a step
    // must not be refused for carrying the version the drawer was opened at.
    vi.mocked(api.addChecklistItem).mockResolvedValue({ ...step("i9", "제출하기", 1), task_version: 2 } as never);
    const { onChanged, onUpdate } = renderDrawer([]);
    vi.mocked(onChanged).mockImplementation(() => new Promise(() => {}));
    const section = await screen.findByLabelText("체크리스트");
    fireEvent.change(within(section).getByLabelText("체크리스트 단계"), { target: { value: "제출하기" } });
    fireEvent.click(within(section).getByRole("button", { name: "추가" }));
    await waitFor(() => expect(within(section).getByText("제출하기")).toBeTruthy());

    const titleField = document.querySelector("input.title-input") as HTMLInputElement;
    fireEvent.change(titleField, { target: { value: "바로 이어서 고친 제목" } });
    fireEvent.click(screen.getByRole("button", { name: "변경 저장" }));
    await waitFor(() => expect(onUpdate).toHaveBeenCalled());
    expect(vi.mocked(onUpdate).mock.calls[0][0].version).toBe(2);
    expect(vi.mocked(onUpdate).mock.calls[0][1]).toEqual({ title: "바로 이어서 고친 제목" });
  });

  it("still accepts the next step while the previous one's refresh is in flight", async () => {
    let release: ((value: unknown) => void) | null = null;
    vi.mocked(api.addChecklistItem)
      .mockResolvedValueOnce(step("i1", "자료 모으기", 1) as never)
      .mockResolvedValueOnce(step("i2", "초안 쓰기", 2) as never);
    const { onChanged } = renderDrawer([]);
    vi.mocked(onChanged).mockImplementation(() => new Promise((resolve) => {
      release = () => resolve(undefined);
    }));
    const section = await screen.findByLabelText("체크리스트");
    const field = within(section).getByLabelText("체크리스트 단계");

    fireEvent.change(field, { target: { value: "자료 모으기" } });
    fireEvent.click(within(section).getByRole("button", { name: "추가" }));
    await waitFor(() => expect(release).not.toBeNull()); // the refresh has not come back yet

    fireEvent.change(field, { target: { value: "초안 쓰기" } });
    fireEvent.click(within(section).getByRole("button", { name: "추가" }));
    await waitFor(() => expect(api.addChecklistItem).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(within(section).getByText("초안 쓰기")).toBeTruthy());
    await act(async () => {
      release?.(undefined);
    });
  });

  it("does not claim the task moved when the server refused the change", async () => {
    vi.mocked(api.removeChecklistItem).mockRejectedValue(new Error("서버 오류"));
    const { onChanged, onError } = renderDrawer([step("i1", "자료 모으기", 1)]);
    const section = await screen.findByLabelText("체크리스트");

    fireEvent.click(within(section).getByRole("button", { name: "자료 모으기 삭제" }));
    await waitFor(() => expect(onError).toHaveBeenCalledWith(expect.stringContaining("서버 오류")));
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("keeps what the user is still typing when only the version moved", async () => {
    vi.mocked(api.addChecklistItem).mockResolvedValue(step("i9", "제출하기", 1) as never);
    const { rerenderWith, onUpdate } = renderDrawer([]);
    const section = await screen.findByLabelText("체크리스트");
    const titleField = document.querySelector("input.title-input") as HTMLInputElement;
    fireEvent.change(titleField, { target: { value: "아직 저장하지 않은 제목" } });

    fireEvent.change(within(section).getByLabelText("체크리스트 단계"), { target: { value: "제출하기" } });
    fireEvent.click(within(section).getByRole("button", { name: "추가" }));
    // The refresh comes back with the same fields at a new version: nothing the user wrote was overwritten.
    rerenderWith({ ...task, version: 2 });

    expect(titleField.value).toBe("아직 저장하지 않은 제목");
    fireEvent.click(screen.getByRole("button", { name: "변경 저장" }));
    await waitFor(() => expect(onUpdate).toHaveBeenCalled());
    expect(vi.mocked(onUpdate).mock.calls[0][0].version).toBe(2); // and the save carries the version the server has
  });

  it("answers the step's own version, so two people editing two steps never collide", async () => {
    vi.mocked(api.updateChecklistItem).mockResolvedValue(step("i2", "초안 쓰기", 2, true, 3) as never);
    renderDrawer([step("i1", "자료 모으기", 1, false, 4), step("i2", "초안 쓰기", 2, false, 2)]);
    const section = await screen.findByLabelText("체크리스트");

    fireEvent.click(within(section).getByRole("checkbox", { name: "초안 쓰기" }));
    await waitFor(() =>
      expect(api.updateChecklistItem).toHaveBeenCalledWith("task-1", "i2", { done: true, expected_version: 2 }),
    );
  });

  it("re-reads the list instead of leaving a step showing what the server refused", async () => {
    vi.mocked(api.updateChecklistItem).mockRejectedValue(new Error("checklist item version is stale"));
    const { onError } = renderDrawer([step("i1", "자료 모으기", 1)]);
    const section = await screen.findByLabelText("체크리스트");
    // What the server has by the time the refusal comes back.
    vi.mocked(api.getTask).mockResolvedValue({ ...task, checklist: [step("i1", "다른 사람이 고친 내용", 1, true, 5)] } as never);

    fireEvent.click(within(section).getByRole("checkbox", { name: "자료 모으기" }));
    await waitFor(() => expect(onError).toHaveBeenCalled());
    expect(vi.mocked(onError).mock.calls.at(-1)?.[0]).toContain("다른 사람이 이 단계를 먼저 고쳤습니다");
    // What the server actually has is on screen, not the change that failed.
    await waitFor(() => expect(within(section).getByText("다른 사람이 고친 내용")).toBeTruthy());
  });

  it("lets someone rename a step in place", async () => {
    vi.mocked(api.updateChecklistItem).mockResolvedValue(step("i1", "자료 정리하기", 1, false, 2) as never);
    renderDrawer([step("i1", "자료 모으기", 1)]);
    const section = await screen.findByLabelText("체크리스트");

    fireEvent.click(within(section).getByRole("button", { name: "자료 모으기 수정" }));
    const field = within(section).getByLabelText("단계 내용") as HTMLInputElement;
    expect(field.value).toBe("자료 모으기");
    fireEvent.change(field, { target: { value: "자료 정리하기" } });
    fireEvent.keyDown(field, { key: "Enter" });
    await waitFor(() =>
      expect(api.updateChecklistItem).toHaveBeenCalledWith("task-1", "i1", { text: "자료 정리하기", expected_version: 1 }),
    );
    await waitFor(() => expect(within(section).getByText("자료 정리하기")).toBeTruthy());
    expect(within(section).queryByLabelText("단계 내용")).toBeNull();

    // Escape leaves the step as it was and sends nothing.
    fireEvent.click(within(section).getByRole("button", { name: "자료 정리하기 수정" }));
    fireEvent.change(within(section).getByLabelText("단계 내용"), { target: { value: "취소될 내용" } });
    fireEvent.keyDown(within(section).getByLabelText("단계 내용"), { key: "Escape" });
    expect(api.updateChecklistItem).toHaveBeenCalledTimes(1);
    expect(within(section).getByText("자료 정리하기")).toBeTruthy();
  });

  it("moves a step with the keyboard and sends the whole order", async () => {
    vi.mocked(api.reorderChecklist).mockResolvedValue({
      task_version: 4,
      checklist: [step("i2", "초안 쓰기", 1), step("i1", "자료 모으기", 2), step("i3", "검토 요청", 3)],
    } as never);
    renderDrawer([step("i1", "자료 모으기", 1), step("i2", "초안 쓰기", 2), step("i3", "검토 요청", 3)]);
    const section = await screen.findByLabelText("체크리스트");

    // The first step cannot move up, and the last cannot move down.
    expect((within(section).getByRole("button", { name: "자료 모으기 위로" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(section).getByRole("button", { name: "검토 요청 아래로" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(within(section).getByRole("button", { name: "초안 쓰기 위로" }));
    await waitFor(() => expect(api.reorderChecklist).toHaveBeenCalledWith("task-1", ["i2", "i1", "i3"]));
    await waitFor(() =>
      expect(Array.from(section.querySelectorAll(".checklist-item span")).map((node) => node.textContent)).toEqual([
        "초안 쓰기",
        "자료 모으기",
        "검토 요청",
      ]),
    );
  });

  it("warns before completing work whose steps are not finished, and still lets it through", async () => {
    const running = { ...task, state: "in_progress" as const };
    vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
    vi.mocked(api.getTask).mockResolvedValue({ ...running, checklist: [step("i1", "자료 모으기", 1), step("i2", "초안 쓰기", 2, true)] } as never);
    const onTransition = vi.fn();
    render(
      <TaskDetailDrawer
        busy={false}
        canManage
        onClose={vi.fn()}
        onError={vi.fn()}
        onNotice={vi.fn()}
        onTransition={onTransition}
        onUpdate={vi.fn()}
        ownerName="민아"
        task={running}
      />,
    );
    await screen.findByLabelText("체크리스트");

    fireEvent.click(screen.getByRole("button", { name: "완료 처리" }));
    expect(onTransition).not.toHaveBeenCalled();
    // It says what is unfinished rather than refusing, because whether that matters is the person's call.
    expect(screen.getByText(/아직 끝나지 않은 단계가 1개/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "그래도 완료" }));
    await waitFor(() => expect(onTransition).toHaveBeenCalledWith(expect.objectContaining({ task_id: "task-1" }), "complete"));
  });

  it("is read-only for someone who cannot manage the task", async () => {
    renderDrawer([step("i1", "자료 모으기", 1)], false);
    const section = await screen.findByLabelText("체크리스트");
    expect((within(section).getByRole("checkbox", { name: "자료 모으기" }) as HTMLInputElement).disabled).toBe(true);
    expect(within(section).queryByLabelText("체크리스트 단계")).toBeNull();
    expect(within(section).queryByRole("button", { name: "자료 모으기 삭제" })).toBeNull();
  });
});

describe("task origin", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders the role the server named, not a guess from a member id", async () => {
    const requested = {
      ...task,
      origin: {
        kind: "work_request",
        actor_role: "요청자",
        actor: { member_id: "mina", display_name: "민아 (구성원)" },
        source: { type: "work_request", id: "r1", title: "견적 재검토" },
      },
    };
    vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
    vi.mocked(api.getTask).mockResolvedValue({ ...requested, checklist: [] } as never);
    render(
      <TaskDetailDrawer busy={false} canManage onClose={vi.fn()} onError={vi.fn()} onNotice={vi.fn()} onTransition={vi.fn()} onUpdate={vi.fn()} ownerName="민아" task={requested as never} />,
    );
    const chip = await screen.findByLabelText("업무 출처");
    expect(chip.textContent).toContain("민아가 보낸 업무");
    expect(within(chip).getByText(/견적 재검토/)).toBeTruthy();
  });

  it("offers no link when the source is withheld, and one when it is allowed", async () => {
    vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
    const withheld = { ...task, origin: { kind: "work_request", actor_role: "요청자", actor: { member_id: "mina", display_name: "민아 (구성원)" }, source: null } };
    vi.mocked(api.getTask).mockResolvedValue({ ...withheld, checklist: [] } as never);
    const onOpenSource = vi.fn();
    const { rerender } = render(
      <TaskDetailDrawer busy={false} canManage onClose={vi.fn()} onError={vi.fn()} onNotice={vi.fn()} onOpenSource={onOpenSource} onTransition={vi.fn()} onUpdate={vi.fn()} ownerName="민아" task={withheld as never} />,
    );
    const chip = await screen.findByLabelText("업무 출처");
    expect(chip.textContent).toContain("민아가 보낸 업무");
    expect(screen.queryByRole("button", { name: /출처 보기|견적/ })).toBeNull();

    const allowed = { ...task, origin: { ...withheld.origin, source: { type: "work_request", id: "r1", title: "견적 재검토" } } };
    rerender(
      <TaskDetailDrawer busy={false} canManage onClose={vi.fn()} onError={vi.fn()} onNotice={vi.fn()} onOpenSource={onOpenSource} onTransition={vi.fn()} onUpdate={vi.fn()} ownerName="민아" task={allowed as never} />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "견적 재검토" }));
    expect(onOpenSource).toHaveBeenCalledWith({ type: "work_request", id: "r1", title: "견적 재검토" });
  });

  it("names the holder from the server projection, not from the origin actor", async () => {
    // On a request-origin task these are different people, which is where a client-side guess went wrong.
    const requested = {
      ...task,
      access: "read_only" as const,
      assignee: { member_id: "jiho", display_name: "지호 (팀장)" },
      origin: { kind: "work_request", actor_role: "요청자", actor: { member_id: "mina", display_name: "민아 (구성원)" }, source: null },
    };
    vi.mocked(api.getTask).mockResolvedValue(requested as never);
    render(
      <TaskDetailDrawer busy={false} canManage={false} onClose={vi.fn()} onError={vi.fn()} onNotice={vi.fn()} onTransition={vi.fn()} onUpdate={vi.fn()} ownerName="지호" task={requested as never} />,
    );
    const assigneeRow = (await screen.findByText("담당자")).closest("div") as HTMLElement;
    expect(within(assigneeRow).getByText("지호")).toBeTruthy();
    const chip = await screen.findByLabelText("업무 출처");
    expect(chip.textContent).toContain("민아가 보낸 업무");
  });

  it("shows a read-only task as a record, not as a workspace", async () => {
    const readOnly = {
      ...task,
      access: "read_only" as const,
      origin: { kind: "work_request", actor_role: "요청자", actor: { member_id: "mina", display_name: "민아 (구성원)" }, source: { type: "work_request", id: "r1", title: "견적 재검토" } },
    };
    vi.mocked(api.getTask).mockResolvedValue(readOnly as never);
    render(
      <TaskDetailDrawer busy={false} canManage={false} onClose={vi.fn()} onError={vi.fn()} onNotice={vi.fn()} onTransition={vi.fn()} onUpdate={vi.fn()} ownerName="민아" task={readOnly as never} />,
    );
    await screen.findByLabelText("업무 출처");
    // The holder's workspace is absent, and the materials are never even fetched.
    expect(screen.queryByLabelText("체크리스트")).toBeNull();
    expect(screen.queryByText("참고 자료")).toBeNull();
    expect(api.getTaskMaterials).not.toHaveBeenCalled();
    expect((screen.getByLabelText("제목") as HTMLInputElement).disabled).toBe(true);
  });

  it("names the assigner for a direct assignment and shows no source when one is withheld", async () => {
    const assigned = {
      ...task,
      origin: { kind: "direct_assignment", actor_role: "배정자", actor: { member_id: "jiho", display_name: "지호 (팀장)" }, source: null },
    };
    vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
    vi.mocked(api.getTask).mockResolvedValue({ ...assigned, checklist: [] } as never);
    render(
      <TaskDetailDrawer busy={false} canManage onClose={vi.fn()} onError={vi.fn()} onNotice={vi.fn()} onTransition={vi.fn()} onUpdate={vi.fn()} ownerName="민아" task={assigned as never} />,
    );
    const chip = await screen.findByLabelText("업무 출처");
    expect(chip.textContent).toContain("지호가 담당자를 지정함");
    expect(chip.textContent).not.toContain("배정자");
    expect(chip.querySelector(".t-meta")).toBeNull(); // nothing stands in for a source the caller may not read
  });
});

describe("what a task detail says about where it came from", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  function renderTask(overrides: Record<string, unknown>, onOpenSource?: () => void) {
    const detail = { ...task, ...overrides };
    vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
    vi.mocked(api.getTask).mockResolvedValue({ ...detail, checklist: [] } as never);
    render(
      <TaskDetailDrawer
        busy={false}
        canManage
        onClose={vi.fn()}
        onError={vi.fn()}
        onNotice={vi.fn()}
        onOpenSource={onOpenSource}
        onTransition={vi.fn()}
        onUpdate={vi.fn()}
        ownerName="민아"
        task={detail as never}
      />,
    );
  }

  it("says nothing about a requester or an assigner when nobody played those parts", async () => {
    renderTask({ origin: null });
    await screen.findByLabelText("업무 상세");
    // The holder is always named; a counterpart is only named when there is one.
    expect(screen.getByText("담당자")).toBeTruthy();
    expect(screen.queryByText("요청자")).toBeNull();
    expect(screen.queryByText("배정자")).toBeNull();
    expect(screen.queryByText("생성자")).toBeNull();
    expect(screen.queryByText("본인 생성")).toBeNull();
    expect(screen.queryByLabelText("업무 출처")).toBeNull();
  });

  it("shows the counterpart and the source as one chip, in the role the server named", async () => {
    renderTask(
      {
        origin: {
          kind: "work_request",
          actor_role: "요청자",
          actor: { member_id: "jiho", display_name: "지호 (팀장)" },
          source: { type: "work_request", id: "r1", title: "견적 재검토" },
        },
      },
      vi.fn(),
    );
    const chip = await screen.findByLabelText("업무 출처");
    // What happened, not which column the server classified it under.
    expect(chip.textContent).toContain("지호가 보낸 업무");
    expect(chip.textContent).not.toContain("요청자");
    expect(within(chip).getByRole("button", { name: /견적 재검토/ })).toBeTruthy();
    // It is a chip beside the work, not a fixed metadata row pretending every task has one.
    expect(screen.queryByRole("term", { name: "요청자" })).toBeNull();
  });

  it("keeps the source chip readable when only the proposal behind it survives", async () => {
    renderTask({
      origin: { kind: "self_created", actor_role: null, actor: null, source: { type: "action_item", id: "a1", title: "AX가 만든 업무" } },
    });
    const chip = await screen.findByLabelText("업무 출처");
    expect(within(chip).getByText(/AX가 만든 업무/)).toBeTruthy();
    expect(chip.textContent).toContain("AX 제안에서 생성됨");
    expect(chip.textContent).not.toContain("생성자");
  });
});

describe("문서 상태", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("says a scan could not be read instead of showing it as an empty success", async () => {
    const { ExtractionStatus } = await import("./WorkModals");
    const { container, rerender } = render(
      <ExtractionStatus
        extraction={{ status: "needs_ocr", chunk_count: 0, char_count: 0, failure_text: "스캔 문서로 보입니다", page_count: 3 } as never}
      />,
    );
    expect(container.textContent).toContain("스캔 문서");
    expect(container.querySelector(".badge.danger")).toBeNull(); // it is not a failure of ours

    rerender(<ExtractionStatus extraction={{ status: "purged", chunk_count: 0, char_count: 0, failure_text: null } as never} />);
    expect(container.textContent).toContain("완전 삭제됨");
  });
});

describe("materials that live somewhere else", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  const link = {
    material_id: "m1",
    attachment_id: "a1",
    task_id: "task-1",
    kind: "input" as const,
    name: "설계 문서",
    content_type: "text/uri-list",
    size_bytes: 0,
    source_kind: "external_link",
    url: "https://docs.example.com/spec",
    mutable_source: true,
    integrity_ref: "observed:2026-09-05T00:00:00Z",
    uploaded_by: "mina",
    created_at: "2026-09-05T00:00:00Z",
    removed_at: null,
    extraction: null,
  };

  function renderWithMaterials(materials: unknown[]) {
    vi.mocked(api.getTaskMaterials).mockResolvedValue(materials as never);
    vi.mocked(api.getTask).mockResolvedValue({ ...task, checklist: [] } as never);
    render(
      <TaskDetailDrawer busy={false} canManage onClose={vi.fn()} onError={vi.fn()} onNotice={vi.fn()} onTransition={vi.fn()} onUpdate={vi.fn()} ownerName="민아" task={task as never} />,
    );
  }

  it("opens a linked material where it lives and says it can change", async () => {
    renderWithMaterials([link]);
    const anchor = (await screen.findByRole("link", { name: "설계 문서" })) as HTMLAnchorElement;
    expect(anchor.href).toBe("https://docs.example.com/spec");
    expect(anchor.rel).toContain("noreferrer");
    const row = anchor.closest("li") as HTMLElement;
    // Nothing was fetched and no revision was pinned, so it must not look like a frozen file.
    expect(within(row).getByText("변경 가능한 링크")).toBeTruthy();
    expect(row.textContent).not.toContain("0 B");
  });

  it("attaches a link with the words a person reads", async () => {
    vi.mocked(api.attachTaskMaterialLink).mockResolvedValue(link as never);
    renderWithMaterials([]);

    // Both sections offer it; this is the 참고 자료 one.
    const inputSection = (await screen.findByText("참고 자료")).closest("section") as HTMLElement;
    fireEvent.click(within(inputSection).getByRole("button", { name: "링크 추가" }));
    fireEvent.change(screen.getByLabelText("참고 자료 링크 주소"), { target: { value: "https://docs.example.com/spec" } });
    fireEvent.change(screen.getByLabelText("참고 자료 링크 이름"), { target: { value: "설계 문서" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "연결" }));
    });

    await waitFor(() => expect(api.attachTaskMaterialLink).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.attachTaskMaterialLink).mock.calls[0]).toEqual([
      "task-1",
      "input",
      { url: "https://docs.example.com/spec", label: "설계 문서" },
    ]);
    expect(await screen.findByRole("link", { name: "설계 문서" })).toBeTruthy();
  });

  it("still downloads a file material through the server", async () => {
    const file = { ...link, material_id: "m2", name: "견적서.pdf", source_kind: "file", url: null, mutable_source: false, size_bytes: 2048 };
    renderWithMaterials([file]);
    const anchor = (await screen.findByRole("link", { name: "견적서.pdf" })) as HTMLAnchorElement;
    expect(anchor.href).toContain("/api/tasks/task-1/materials/m2/content");
    expect(screen.queryByText("변경 가능한 링크")).toBeNull();
  });
});

describe("materials that point at other work in SCAX", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  const reference = {
    material_id: "m3",
    attachment_id: "a3",
    task_id: "task-1",
    kind: "input" as const,
    name: "먼저 한 업무",
    content_type: "application/vnd.scax.resource-ref",
    size_bytes: 0,
    source_kind: "resource_ref",
    url: null,
    resource: { type: "task", id: "task-9", title: "먼저 한 업무" },
    mutable_source: true,
    integrity_ref: "observed:2026-09-05T00:00:00Z",
    uploaded_by: "mina",
    created_at: "2026-09-05T00:00:00Z",
    removed_at: null,
    extraction: null,
  };

  function renderWithMaterials(materials: unknown[], onOpenTask?: (taskId: string) => void) {
    vi.mocked(api.getTaskMaterials).mockResolvedValue(materials as never);
    vi.mocked(api.getTask).mockResolvedValue({ ...task, checklist: [] } as never);
    render(
      <TaskDetailDrawer
        busy={false}
        canManage
        onClose={vi.fn()}
        onError={vi.fn()}
        onNotice={vi.fn()}
        onOpenTask={onOpenTask}
        onTransition={vi.fn()}
        onUpdate={vi.fn()}
        ownerName="민아"
        task={task as never}
      />,
    );
  }

  it("opens the referenced work inside the product instead of leaving for a URL", async () => {
    const onOpenTask = vi.fn();
    renderWithMaterials([reference], onOpenTask);
    const open = await screen.findByRole("button", { name: "먼저 한 업무" });
    fireEvent.click(open);
    expect(onOpenTask).toHaveBeenCalledWith("task-9");
    expect(screen.queryByRole("link", { name: "먼저 한 업무" })).toBeNull();
  });

  it("says a reference is withheld rather than showing a title the reader cannot open", async () => {
    renderWithMaterials([{ ...reference, name: "볼 수 없는 자료", resource: null }]);
    await screen.findByText("볼 수 없는 자료");
    expect(screen.queryByRole("button", { name: "먼저 한 업무" })).toBeNull();
    expect(document.body.textContent).not.toContain("먼저 한 업무");
  });

  it("no longer offers work as a material: earlier work is connected as 참고 업무 instead", async () => {
    renderWithMaterials([]);
    const inputSection = (await screen.findByText("참고 자료")).closest("section") as HTMLElement;
    expect(within(inputSection).queryByRole("button", { name: "업무 연결" })).toBeNull();
    // The one place that connects work to work is its own section.
    const references = screen.getByLabelText("참고 업무");
    expect(within(references).getByRole("button", { name: "업무 연결" })).toBeTruthy();
  });
});

describe("changing who holds the work", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  function renderDrawer(props: Record<string, unknown> = {}) {
    vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
    vi.mocked(api.getTask).mockResolvedValue({ ...task, checklist: [] } as never);
    const onNotice = vi.fn();
    render(
      <TaskDetailDrawer
        busy={false}
        canManage
        onClose={vi.fn()}
        onError={vi.fn()}
        onNotice={onNotice}
        onTransition={vi.fn()}
        onUpdate={vi.fn()}
        ownerName="민아"
        task={task as never}
        {...props}
      />,
    );
    return { onNotice };
  }

  it("is its own command, never a field on the task form", async () => {
    vi.mocked(api.getTaskAssignmentCandidates).mockResolvedValue([{ id: "jiho", display_name: "지호 (팀장)" }] as never);
    renderDrawer({ canAssign: true });
    await screen.findByLabelText("업무 상세");
    // The edit form has no assignee input; moving the work is a separate action.
    expect(screen.queryByLabelText("담당자 변경 대상")).toBeNull();
    expect(screen.getByRole("button", { name: "담당자 변경" })).toBeTruthy();
  });

  it("moves the work to someone else with a reason and the version it was shown", async () => {
    vi.mocked(api.getTaskAssignmentCandidates).mockResolvedValue([{ id: "jiho", display_name: "지호 (팀장)" }] as never);
    vi.mocked(api.reassignTask).mockResolvedValue({ assignment_id: "as-2", assignee_id: "jiho" } as never);
    const onChanged = vi.fn();
    const { onNotice } = renderDrawer({ canAssign: true, onChanged });

    fireEvent.click(await screen.findByRole("button", { name: "담당자 변경" }));
    fireEvent.change(await screen.findByLabelText("담당자 변경 대상"), { target: { value: "jiho" } });
    fireEvent.change(screen.getByLabelText("담당자 변경 사유"), { target: { value: "제가 이어서 합니다" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "변경" }));
    });

    await waitFor(() => expect(api.reassignTask).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.reassignTask).mock.calls[0]).toEqual(["task-1", 1, "jiho", "제가 이어서 합니다"]);
    expect(onNotice).toHaveBeenCalledWith(expect.stringContaining("담당자"));
  });

  it("is not offered to someone who may not put people on work", async () => {
    renderDrawer({ canAssign: false });
    await screen.findByLabelText("업무 상세");
    expect(screen.queryByRole("button", { name: "담당자 변경" })).toBeNull();
  });
});
