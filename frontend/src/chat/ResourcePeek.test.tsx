import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  getTask: vi.fn(),
  getMeeting: vi.fn(),
  getTaskMaterials: vi.fn(),
  getMaterialMetadata: vi.fn(),
  getWorkRequestTimeline: vi.fn(),
}));

import * as api from "../api";
import { ResourcePeek } from "./ResourcePeek";

const reference = {
  reference_id: "a1",
  turn_id: "t",
  sequence: 1,
  resource_type: "task" as const,
  resource_id: "task-1",
  resource_version: 3,
  title: "분기 마감",
  state: "in_progress",
};

describe("근거 상세", () => {
  afterEach(cleanup);

  it("대화를 떠나지 않고 지금의 정본을 읽어 보여 준다", async () => {
    vi.mocked(api.getTask).mockResolvedValue({
      task_id: "task-1",
      title: "분기 마감",
      state: "in_progress",
      due_date: "2026-09-30",
      description: "정산 자료 대조",
      assignee: { member_id: "mina", display_name: "민아 (구성원)" },
    } as never);
    render(<ResourcePeek onClose={vi.fn()} onOpenFully={vi.fn()} resource={reference} />);

    await screen.findByText("진행 중");
    expect(screen.getByText("정산 자료 대조")).toBeTruthy();
    // 요약이 아니라 그 원장이 지금 답하는 것이다.
    expect(api.getTask).toHaveBeenCalledWith("task-1");
  });

  it("답변 뒤에 바뀌었으면 그 사실을 먼저 말한다", async () => {
    vi.mocked(api.getTask).mockResolvedValue({ task_id: "task-1", title: "분기 마감", state: "done" } as never);
    render(<ResourcePeek onClose={vi.fn()} onOpenFully={vi.fn()} resource={{ ...reference, changed_since: true }} />);
    expect(screen.getByText(/답변 뒤에 바뀌었습니다/)).toBeTruthy();
  });

  it("열 수 없게 되었으면 왜인지는 말하지 않는다", async () => {
    vi.mocked(api.getTask).mockRejectedValue(new Error("403"));
    render(<ResourcePeek onClose={vi.fn()} onOpenFully={vi.fn()} resource={reference} />);
    await screen.findByText("지금은 열 수 없습니다.");
    // 사라진 것과 권한을 잃은 것을 구별해 말하지 않는다 — 구별이 곧 존재를 알리는 말이 된다.
    expect(screen.queryByText(/권한|삭제|없는/)).toBeNull();
  });

  it("canonical 자료를 현재 권한으로 다시 읽는다", async () => {
    vi.mocked(api.getMaterialMetadata).mockResolvedValue({ material_id: "artifact-1", name: "제출 원본", origin: "/api/source", integrity_ref: "sha256:original" });
    render(<ResourcePeek onClose={vi.fn()} onOpenFully={vi.fn()} resource={{ ...reference, resource_type: "material", resource_id: "artifact-1" }} />);
    await screen.findByText("제출 원본");
    expect(api.getMaterialMetadata).toHaveBeenCalledWith("artifact-1");
    expect(screen.getByText("sha256:original")).toBeTruthy();
  });

  it("canonical 자료를 더 이상 읽을 수 없으면 내용을 표시하지 않는다", async () => {
    vi.mocked(api.getMaterialMetadata).mockRejectedValue(new Error("404"));
    render(<ResourcePeek onClose={vi.fn()} onOpenFully={vi.fn()} resource={{ ...reference, resource_type: "material", resource_id: "artifact-1" }} />);
    await screen.findByText("지금은 열 수 없습니다.");
    expect(screen.queryByText("sha256:original")).toBeNull();
  });

  it("정식 화면으로 가는 길은 따로 있다", async () => {
    vi.mocked(api.getTask).mockResolvedValue({ task_id: "task-1", title: "분기 마감", state: "open" } as never);
    const onOpenFully = vi.fn();
    const onClose = vi.fn();
    render(<ResourcePeek onClose={onClose} onOpenFully={onOpenFully} resource={reference} />);
    fireEvent.click(screen.getByText("정식 화면에서 열기"));
    await waitFor(() => expect(onOpenFully).toHaveBeenCalledWith(reference));
    fireEvent.click(screen.getByLabelText("근거 상세 닫기"));
    expect(onClose).toHaveBeenCalled();
  });
});
