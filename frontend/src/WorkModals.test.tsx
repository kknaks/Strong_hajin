import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Persona, WorkRequest } from "./viewModels";

vi.mock("./api", () => ({
  addWorkRequestComment: vi.fn(),
  getWorkRequestTimeline: vi.fn(),
  uploadCommentAttachment: vi.fn(),
  requestAttachmentUrl: () => "",
  resubmitWorkRequest: vi.fn(),
  decideWorkRequest: vi.fn(),
  negotiateWorkRequest: vi.fn(),
  uploadRequestEvidence: vi.fn(),
}));

import * as api from "./api";
import { WorkRequestDetailDrawer } from "./WorkModals";

const request: WorkRequest = {
  request_id: "request-1",
  title: "견적 재검토",
  description: "9월 견적 재검토",
  state: "negotiating",
  version: 3,
  task_id: null,
  assignment_state: null,
  requester_id: "mina",
  assignee_id: "jiho",
  due_date: null,
  conditions: { note: "기한을 늦춰 주세요" },
} as WorkRequest;

const personas: Persona[] = [
  { id: "mina", display_name: "민아 (구성원)" },
  { id: "jiho", display_name: "지호 (팀장)" },
];

const emptyTimeline = {
  request,
  request_thread_id: "thread-1",
  comments: [],
  evidence: [],
  decision_item: null,
  submissions: [],
  review_assignments: [],
  review_decisions: [],
  activity: [],
};

function renderDrawer(overrides: Partial<Parameters<typeof WorkRequestDetailDrawer>[0]> = {}) {
  const props = {
    request,
    personaId: "mina",
    personas,
    canDecide: false,
    onChanged: vi.fn(),
    onError: vi.fn(),
    onNotice: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
  return { ...render(<WorkRequestDetailDrawer {...props} />), props };
}

describe("work request comments", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("sends exactly one comment when Enter and the button fire before React re-renders", async () => {
    vi.mocked(api.getWorkRequestTimeline).mockResolvedValue(emptyTimeline as never);
    let release: (() => void) | null = null;
    vi.mocked(api.addWorkRequestComment).mockImplementation(
      () => new Promise((resolve) => {
        release = () => resolve({ comment_id: "comment-1", body: "논의 추가" } as never);
      }),
    );
    renderDrawer();
    const field = await screen.findByLabelText("댓글");
    fireEvent.change(field, { target: { value: "논의 추가" } });

    // Same tick: Enter, an auto-repeat Enter, and a click on the still-enabled button.
    fireEvent.keyDown(field, { key: "Enter" });
    fireEvent.keyDown(field, { key: "Enter", repeat: true });
    fireEvent.click(screen.getByRole("button", { name: "남기기" }));

    expect(api.addWorkRequestComment).toHaveBeenCalledTimes(1);
    const [, body, key] = vi.mocked(api.addWorkRequestComment).mock.calls[0];
    expect(body).toBe("논의 추가");
    expect(key).toBeTruthy(); // one key for the logical submit
    await act(async () => {
      release?.();
    });
    await waitFor(() => expect((field as HTMLInputElement).value).toBe(""));
    expect(api.addWorkRequestComment).toHaveBeenCalledTimes(1);
  });

  it("prevents the default Enter action so the field cannot submit twice through the form", async () => {
    vi.mocked(api.getWorkRequestTimeline).mockResolvedValue(emptyTimeline as never);
    vi.mocked(api.addWorkRequestComment).mockResolvedValue({ comment_id: "c", body: "x" } as never);
    renderDrawer();
    const field = await screen.findByLabelText("댓글");
    fireEvent.change(field, { target: { value: "논의 추가" } });
    const prevented = !fireEvent.keyDown(field, { key: "Enter" });
    expect(prevented).toBe(true);
  });

  it("reuses one key while the text is unchanged and takes a new key for new text", async () => {
    vi.mocked(api.getWorkRequestTimeline).mockResolvedValue(emptyTimeline as never);
    vi.mocked(api.addWorkRequestComment).mockRejectedValueOnce(new Error("네트워크 오류"));
    const onError = vi.fn();
    renderDrawer({ onError });
    const field = await screen.findByLabelText("댓글");
    fireEvent.change(field, { target: { value: "논의 추가" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "남기기" }));
    });
    await waitFor(() => expect(onError).toHaveBeenCalled());
    const firstKey = vi.mocked(api.addWorkRequestComment).mock.calls[0][2];

    // Retrying the same text must resolve to the same server comment, so the key is reused.
    vi.mocked(api.addWorkRequestComment).mockResolvedValue({ comment_id: "c1", body: "논의 추가" } as never);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "남기기" }));
    });
    await waitFor(() => expect(api.addWorkRequestComment).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.addWorkRequestComment).mock.calls[1][2]).toBe(firstKey);

    // A new logical submit gets its own key; the same words may legitimately be said twice.
    fireEvent.change(field, { target: { value: "두 번째 발언" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "남기기" }));
    });
    await waitFor(() => expect(api.addWorkRequestComment).toHaveBeenCalledTimes(3));
    expect(vi.mocked(api.addWorkRequestComment).mock.calls[2][2]).not.toBe(firstKey);
  });
});
