import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Persona, WorkRequest } from "../../lib/viewModels";

vi.mock("../../lib/api", () => ({
  addWorkRequestComment: vi.fn(),
  getWorkRequestTimeline: vi.fn(),
  uploadCommentAttachment: vi.fn(),
  requestAttachmentUrl: () => "",
  resubmitWorkRequest: vi.fn(),
  decideWorkRequest: vi.fn(),
  negotiateWorkRequest: vi.fn(),
  uploadRequestEvidence: vi.fn(),
  amendWorkRequest: vi.fn(),
}));

import * as api from "../../lib/api";
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

describe("work request contents", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows the requested checklist and permission-safe reference tasks in the detail drawer", async () => {
    vi.mocked(api.getWorkRequestTimeline).mockResolvedValue(emptyTimeline as never);
    const onOpenDerivedTask = vi.fn();
    renderDrawer({
      onOpenDerivedTask,
      request: {
        ...request,
        checklist: ["자료 수집", "수치 검토"],
        references: [
          {
            reference_id: "reference-1",
            created_by: "mina",
            task: { task_id: "task-1", title: "지난 분기 보고", state: "done", due_date: "2026-08-31" },
          },
          { reference_id: "reference-2", created_by: "mina", task: null },
        ],
      },
    });

    const checklist = await screen.findByRole("list", { name: "요청 체크리스트" });
    expect(within(checklist).getByText("자료 수집")).toBeTruthy();
    expect(within(checklist).getByText("수치 검토")).toBeTruthy();
    const references = screen.getByRole("list", { name: "요청 참고 업무" });
    fireEvent.click(within(references).getByRole("button", { name: "지난 분기 보고 열기" }));
    expect(onOpenDerivedTask).toHaveBeenCalledWith("task-1");
    expect(within(references).getByText("볼 수 없는 업무")).toBeTruthy();
  });
});

describe("request round history", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("formats dates inside a round diff instead of showing the stored ISO value", async () => {
    vi.mocked(api.getWorkRequestTimeline).mockResolvedValue({
      ...emptyTimeline,
      submissions: [
        { submission_id: "s1", submission_version: 1, revises_id: null, submitted_by: "mina", submitted_at: "2026-09-01T00:00:00Z", snapshot: {}, subject_version: null, diff: null },
        {
          submission_id: "s2",
          submission_version: 2,
          revises_id: "s1",
          submitted_by: "mina",
          submitted_at: "2026-09-03T00:00:00Z",
          snapshot: {},
          subject_version: null,
          diff: { due_date: { before: "2026-09-20", after: "2026-09-30" }, title: { before: "이전 제목", after: "새 제목" } },
        },
      ],
    } as never);
    const { container } = renderDrawer();
    await screen.findByText(/2회차|제출 v2|s2/i).catch(() => null);
    const diff = await waitFor(() => {
      const found = container.querySelector(".diff-list");
      if (!found) throw new Error("diff not rendered");
      return found as HTMLElement;
    });
    expect(diff.textContent).toContain("2026/09/20");
    expect(diff.textContent).toContain("2026/09/30");
    expect(diff.textContent).not.toMatch(/2026-09-\d\d/);
    // Non-date fields are untouched.
    expect(diff.textContent).toContain("이전 제목");
    expect(diff.textContent).toContain("새 제목");
  });
});

describe("adopting evidence", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  const pending = { ...request, state: "pending", version: 3 } as WorkRequest;

  it("offers to adopt evidence only while the round is still open to it", async () => {
    vi.mocked(api.getWorkRequestTimeline).mockResolvedValue(emptyTimeline as never);
    for (const [state, offered] of [
      ["pending", true],
      ["negotiating", false],
      ["accepted", false],
      ["rejected", false],
      ["withdrawn", false],
    ] as const) {
      cleanup();
      renderDrawer({ request: { ...request, state } as WorkRequest, personaId: "mina" });
      await screen.findAllByText(/근거 자료/);
      expect(screen.queryByLabelText("근거 자료 파일") !== null).toBe(offered);
    }

    // Nor is it offered to someone who is neither the requester nor the assignee.
    cleanup();
    renderDrawer({ request: pending, personaId: "demo-admin" });
    await screen.findAllByText(/근거 자료/);
    expect(screen.queryByLabelText("근거 자료 파일")).toBeNull();
  });

  it("settles the request version after adopting, so the same person can decide right away", async () => {
    vi.mocked(api.getWorkRequestTimeline).mockResolvedValue(emptyTimeline as never);
    vi.mocked(api.uploadRequestEvidence).mockResolvedValue({
      evidence_id: "evidence-1",
      submission_id: "submission-1",
      submission_version: 1,
      attachment_id: "attachment-1",
      name: "근거.txt",
      content_type: "text/plain",
      size_bytes: 4,
      evidence_role: "supporting",
      fixed_snapshot_ref: "sha256:abc",
      adopted_by: "mina",
      adopted_at: "2026-09-05T00:00:00Z",
      request_version: 4,
    } as never);
    const { props } = renderDrawer({ request: pending, personaId: "mina" });
    const field = (await screen.findByLabelText("근거 자료 파일")) as HTMLInputElement;

    await act(async () => {
      fireEvent.change(field, { target: { files: [new File(["body"], "근거.txt", { type: "text/plain" })] } });
    });

    await waitFor(() => expect(api.uploadRequestEvidence).toHaveBeenCalledTimes(1));
    // Adopting moves the request on, so the drawer takes the new version before anything is decided on it.
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled());
    expect(vi.mocked(api.getWorkRequestTimeline).mock.calls.length).toBeGreaterThan(1);
    expect(props.onNotice).toHaveBeenCalledWith(expect.stringContaining("근거.txt"));
  });

  it('keeps request evidence upload alive when the drawer or window is closed', async () => {
    vi.mocked(api.getWorkRequestTimeline).mockResolvedValue(emptyTimeline as never);
    let reject!: (error: Error) => void;
    vi.mocked(api.uploadRequestEvidence).mockReturnValue(new Promise((_, fail) => { reject = fail; }));
    const { props } = renderDrawer({ request: pending, personaId: 'mina' });
    const field = await screen.findByLabelText('근거 자료 파일');
    fireEvent.change(field, { target: { files: [new File(['basis'], 'basis.txt')] } });
    await waitFor(() => expect(api.uploadRequestEvidence).toHaveBeenCalled());
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: '상세 닫기' }));
    expect(props.onClose).not.toHaveBeenCalled();
    const leaving = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(leaving);
    expect(leaving.defaultPrevented).toBe(true);
    reject(new Error('파일 저장 실패'));
    await waitFor(() => expect(props.onError).toHaveBeenCalledWith('파일 저장 실패'));
    fireEvent.click(screen.getByRole('button', { name: '상세 닫기' }));
    expect(props.onClose).toHaveBeenCalledOnce();
  });
});

describe("amending a request nobody has judged yet", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  const pending = { ...request, state: "pending", version: 4, conditions: null } as WorkRequest;

  it("offers 요청 수정 to the requester only while the request is still open", async () => {
    vi.mocked(api.getWorkRequestTimeline).mockResolvedValue(emptyTimeline as never);
    for (const [state, personaId, offered] of [
      ["pending", "mina", true],
      ["pending", "jiho", false],
      ["negotiating", "mina", false],
      ["accepted", "mina", false],
      ["withdrawn", "mina", false],
    ] as const) {
      cleanup();
      renderDrawer({ request: { ...pending, state } as WorkRequest, personaId });
      await screen.findAllByText(/근거 자료/);
      expect(screen.queryByRole("button", { name: "요청 수정" }) !== null).toBe(offered);
    }
  });

  it("shows what will change before sending, and sends only the fields that differ", async () => {
    vi.mocked(api.getWorkRequestTimeline).mockResolvedValue(emptyTimeline as never);
    vi.mocked(api.amendWorkRequest).mockResolvedValue({ ...pending, title: "보강한 요청", version: 5 } as never);
    const { props } = renderDrawer({ request: pending, personaId: "mina" });

    fireEvent.click(await screen.findByRole("button", { name: "요청 수정" }));
    const title = (await screen.findByLabelText("요청할 업무")) as HTMLInputElement;
    expect(title.value).toBe("견적 재검토");
    // Nothing has changed yet, so there is nothing to send.
    expect(screen.getByText(/바뀐 내용이 없습니다/)).toBeTruthy();

    fireEvent.change(title, { target: { value: "보강한 요청" } });
    const summary = screen.getByLabelText("제출 전 변경 요약");
    expect(within(summary).getByText(/견적 재검토/)).toBeTruthy();
    expect(within(summary).getByText(/보강한 요청/)).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "수정 제출" }));
    });
    await waitFor(() => expect(api.amendWorkRequest).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.amendWorkRequest).mock.calls[0]).toEqual(["request-1", 4, { title: "보강한 요청" }]);
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled());
  });
});
