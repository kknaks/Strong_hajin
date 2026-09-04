import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DirectTask } from "./viewModels";

vi.mock("./api", () => ({
  getTask: vi.fn(),
  getTaskMaterials: vi.fn(),
  addChecklistItem: vi.fn(),
  updateChecklistItem: vi.fn(),
  removeChecklistItem: vi.fn(),
  uploadTaskMaterial: vi.fn(),
  detachTaskMaterial: vi.fn(),
  taskMaterialContentUrl: () => "",
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

const step = (id: string, text: string, position: number, done = false) => ({
  item_id: id,
  text,
  position,
  done,
  completed_by: done ? "mina" : null,
  completed_at: done ? "2026-09-04T01:00:00Z" : null,
});

function renderDrawer(items: ReturnType<typeof step>[], canManage = true) {
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getTask).mockResolvedValue({ ...task, checklist: items } as never);
  const onError = vi.fn();
  render(
    <TaskDetailDrawer
      busy={false}
      canManage={canManage}
      onClose={vi.fn()}
      onError={onError}
      onNotice={vi.fn()}
      onTransition={vi.fn()}
      onUpdate={vi.fn()}
      ownerName="민아"
      task={task}
    />,
  );
  return { onError };
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
    await waitFor(() => expect(api.updateChecklistItem).toHaveBeenCalledWith("task-1", "i2", { done: true }));
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
    await waitFor(() => expect(onError).toHaveBeenCalledWith("서버 오류"));
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
    const row = (await screen.findByText("요청자")).closest("div") as HTMLElement;
    expect(within(row).getByText("민아")).toBeTruthy();
    expect(within(row).getByText(/견적 재검토/)).toBeTruthy();
  });

  it("offers no link when the source is withheld, and one when it is allowed", async () => {
    vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
    const withheld = { ...task, origin: { kind: "work_request", actor_role: "요청자", actor: { member_id: "mina", display_name: "민아 (구성원)" }, source: null } };
    vi.mocked(api.getTask).mockResolvedValue({ ...withheld, checklist: [] } as never);
    const onOpenSource = vi.fn();
    const { rerender } = render(
      <TaskDetailDrawer busy={false} canManage onClose={vi.fn()} onError={vi.fn()} onNotice={vi.fn()} onOpenSource={onOpenSource} onTransition={vi.fn()} onUpdate={vi.fn()} ownerName="민아" task={withheld as never} />,
    );
    await screen.findByText("요청자");
    expect(screen.queryByRole("button", { name: /출처 보기|견적/ })).toBeNull();

    const allowed = { ...task, origin: { ...withheld.origin, source: { type: "work_request", id: "r1", title: "견적 재검토" } } };
    rerender(
      <TaskDetailDrawer busy={false} canManage onClose={vi.fn()} onError={vi.fn()} onNotice={vi.fn()} onOpenSource={onOpenSource} onTransition={vi.fn()} onUpdate={vi.fn()} ownerName="민아" task={allowed as never} />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "견적 재검토" }));
    expect(onOpenSource).toHaveBeenCalledWith({ type: "work_request", id: "r1", title: "견적 재검토" });
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
    await screen.findByText("요청자");
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
    const row = (await screen.findByText("배정자")).closest("div") as HTMLElement;
    expect(within(row).getByText("지호")).toBeTruthy();
    expect(screen.queryByText("요청자")).toBeNull();
    expect(row.querySelector(".t-meta")).toBeNull(); // nothing stands in for a source the caller may not read
  });
});
