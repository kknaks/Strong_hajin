import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    await waitFor(() => expect(within(section).getByText("· 1/3")).toBeTruthy());
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
    await waitFor(() => expect(within(section).getByText("· 0/1")).toBeTruthy());
  });

  it("checks a step off and moves the progress with the server's answer", async () => {
    vi.mocked(api.updateChecklistItem).mockResolvedValue(step("i2", "초안 쓰기", 2, true) as never);
    renderDrawer([step("i1", "자료 모으기", 1), step("i2", "초안 쓰기", 2)]);
    const section = await screen.findByLabelText("체크리스트");
    await waitFor(() => expect(within(section).getByText("· 0/2")).toBeTruthy());

    fireEvent.click(within(section).getByRole("checkbox", { name: "초안 쓰기" }));
    await waitFor(() => expect(api.updateChecklistItem).toHaveBeenCalledWith("task-1", "i2", { done: true }));
    await waitFor(() => expect(within(section).getByText("· 1/2")).toBeTruthy());
    expect((section.querySelector('[data-item-id="i2"]') as HTMLElement).className).toContain("done");
  });

  it("removes a step and reports a failure instead of dropping it from the view", async () => {
    vi.mocked(api.removeChecklistItem).mockResolvedValueOnce(undefined as never).mockRejectedValueOnce(new Error("서버 오류"));
    const { onError } = renderDrawer([step("i1", "자료 모으기", 1), step("i2", "초안 쓰기", 2)]);
    const section = await screen.findByLabelText("체크리스트");

    fireEvent.click(within(section).getByRole("button", { name: "자료 모으기 삭제" }));
    await waitFor(() => expect(section.querySelector('[data-item-id="i1"]')).toBeNull());
    await waitFor(() => expect(within(section).getByText("· 0/1")).toBeTruthy());

    fireEvent.click(within(section).getByRole("button", { name: "초안 쓰기 삭제" }));
    await waitFor(() => expect(onError).toHaveBeenCalledWith("서버 오류"));
    expect(section.querySelector('[data-item-id="i2"]')).toBeTruthy(); // still there, because the server refused
  });

  it("is read-only for someone who cannot manage the task", async () => {
    renderDrawer([step("i1", "자료 모으기", 1)], false);
    const section = await screen.findByLabelText("체크리스트");
    expect((within(section).getByRole("checkbox", { name: "자료 모으기" }) as HTMLInputElement).disabled).toBe(true);
    expect(within(section).queryByLabelText("체크리스트 단계")).toBeNull();
    expect(within(section).queryByRole("button", { name: "자료 모으기 삭제" })).toBeNull();
  });
});
