import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", () => ({
  createDirectTask: vi.fn(),
  createWorkRequest: vi.fn(),
  assignTask: vi.fn(),
  uploadTaskMaterial: vi.fn(),
  getTasks: vi.fn(),
  listProjects: vi.fn(),
  getTask: vi.fn(),
  getTaskMaterials: vi.fn(),
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
  submitTaskCompletion: vi.fn(),
  addTaskReference: vi.fn(),
  releaseTaskReference: vi.fn(),
  getTaskHistory: vi.fn(),
  getTaskHistoryDiff: vi.fn(),
  addChecklistItem: vi.fn(),
  reorderChecklist: vi.fn(),
  updateChecklistItem: vi.fn(),
  removeChecklistItem: vi.fn(),
  detachTaskMaterial: vi.fn(),
  attachTaskMaterialLink: vi.fn(),
  attachTaskMaterialReference: vi.fn(),
  getTaskAssignmentCandidates: vi.fn(),
  reassignTask: vi.fn(),
  taskMaterialContentUrl: () => "",
  addWorkRequestComment: vi.fn(),
  getWorkRequestTimeline: vi.fn(),
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

/**
 * 생성 모달의 **첨부파일** — 두 단계다 (WORK Phase 7-A).
 *
 * 자료는 업무에 매달리므로 **업무가 서야 붙일 자리가 생긴다.** 그래서 이 화면은 «고르기» 까지이고,
 * 업로드는 생성 성공 직후에 일어난다. 그 구조가 만드는 두 가지 위험을 여기서 못박는다 —
 * ① 생성 뒤 업로드가 실패했을 때 **업무가 둘 서는 것**, ② 붙일 수 없는 갈래에서 고른 파일이
 * **조용히 사라지는 것**.
 *
 * 갈래마다 붙일 수 있는지가 다른 이유는 서버의 쓰기 권한이다 — `materials.upload` 가
 * `work_tasks.task(task_id, principal)`(활성 담당)로 가므로, 만든 사람이 그 업무를 들지 않는
 * 갈래(관리자 배정 · 요청 발송 · 회의 승격)에서는 **붙일 사람이 그 사람이 아니다.**
 */

const sora = { id: "sora", display_name: "소라 (기획)" };
const jiho = { id: "jiho", display_name: "지호 (팀장)" };

function renderModal(overrides: Record<string, unknown> = {}) {
  vi.mocked(api.getTasks).mockResolvedValue([]);
  vi.mocked(api.listProjects).mockResolvedValue([]);
  const onCreated = vi.fn();
  const onError = vi.fn();
  const onClose = vi.fn();
  const onOpenTask = vi.fn();
  render(
    <CreateWorkModal
      assignCandidates={[]}
      assigneeCandidates={[sora]}
      canCreateRequest
      canCreateTask
      onClose={onClose}
      onCreated={onCreated}
      onError={onError}
      onOpenTask={onOpenTask}
      ownerName="민아"
      {...overrides}
    />,
  );
  return { onCreated, onError, onClose, onOpenTask };
}

const file = (name: string) => new File(["x"], name, { type: "text/plain" });

function pickFiles(names: string[]) {
  const input = screen.getByLabelText("파일 추가") as HTMLInputElement;
  fireEvent.change(input, { target: { files: names.map(file) } });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("본인 업무 — 고른 파일이 생성 직후 자료로 붙는다", () => {
  it("업무를 만든 뒤 그 task_id 로 자료를 올린다 — 새 서버 API 없이 두 단계다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-9", title: "계약서 검토" } as never);
    vi.mocked(api.uploadTaskMaterial).mockResolvedValue({} as never);
    const { onCreated, onClose } = renderModal();

    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "계약서 검토" } });
    pickFiles(["초안.pdf", "의견.md"]);
    expect(within(screen.getByLabelText("첨부할 파일")).getAllByRole("listitem")).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    await waitFor(() => expect(api.uploadTaskMaterial).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.uploadTaskMaterial).mock.calls.map((call) => [call[0], call[1]])).toEqual([
      ["task-9", "input"],
      ["task-9", "input"],
    ]);
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("'계약서 검토' 업무를 만들었습니다. 첨부 2건을 올렸습니다.", { assignedToOther: false }));
    expect(onClose).toHaveBeenCalled();
  });

  it("첨부가 없으면 업로드를 부르지 않는다 — 기존 생성 경로 그대로다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-9", title: "계약서 검토" } as never);
    const { onCreated } = renderModal();
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "계약서 검토" } });
    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("'계약서 검토' 업무를 만들었습니다.", { assignedToOther: false }));
    expect(api.uploadTaskMaterial).not.toHaveBeenCalled();
  });
});

describe("생성은 됐는데 첨부가 실패한 자리", () => {
  /**
   * **여기서 업무가 둘 서면 안 된다.** 생성은 이미 끝났으므로 다시 누르는 것은 «업로드 재시도» 이지
   * 「처음부터 다시」가 아니다.
   */
  it("다시 시도는 업로드만 다시 하고 업무를 다시 만들지 않는다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-9", title: "계약서 검토" } as never);
    vi.mocked(api.uploadTaskMaterial).mockRejectedValueOnce(new Error("서버 오류")).mockResolvedValueOnce({} as never);
    const { onCreated, onError, onClose } = renderModal();

    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "계약서 검토" } });
    pickFiles(["초안.pdf"]);
    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    // 만들어진 사실은 먼저 알린다 — 목록이 그 업무를 들고 있어야 한다.
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("'계약서 검토' 업무를 만들었습니다.", { assignedToOther: false }));
    // 그러나 닫지 않는다: 첨부가 남았다는 사실을 사람이 보고 골라야 한다.
    expect(onClose).not.toHaveBeenCalled();
    expect(vi.mocked(onError).mock.calls.at(-1)?.[0]).toContain("업무는 만들어졌지만 첨부 1건을 올리지 못했습니다");

    const retry = await screen.findByRole("button", { name: "첨부 다시 시도 (1)" });
    fireEvent.click(retry);

    await waitFor(() => expect(api.uploadTaskMaterial).toHaveBeenCalledTimes(2));
    // **생성은 여전히 한 번이다.**
    expect(api.createDirectTask).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("「업무 열기」로 상세에 가서 붙일 수 있다 — 그때도 업무를 다시 만들지 않는다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-9", title: "계약서 검토" } as never);
    vi.mocked(api.uploadTaskMaterial).mockRejectedValue(new Error("서버 오류"));
    const { onOpenTask } = renderModal();

    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "계약서 검토" } });
    pickFiles(["초안.pdf"]);
    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    fireEvent.click(await screen.findByRole("button", { name: "업무 열기" }));
    expect(onOpenTask).toHaveBeenCalledWith("task-9");
    expect(api.createDirectTask).toHaveBeenCalledTimes(1);
  });
});

describe("붙일 수 없는 갈래 — 권한 경계를 말하고, 고른 파일을 버리지 않는다", () => {
  /**
   * 서버의 업로드는 **활성 담당** 에게만 열린다. 보내는 사람은 그 자리가 아니므로, 「생성 뒤 상세에서
   * 붙이면 된다」고만 쓰면 **보내는 사람도 할 수 있는 것처럼 읽혀 틀린다.**
   */
  it("요청 갈래에서는 담당자가 수락한 뒤 붙는다고 말한다", async () => {
    renderModal({ canCreateTask: false });
    const field = screen.getByLabelText("첨부파일");
    expect(within(field).getByText(/자료는 담당자가 업무 상세에서 첨부할 수 있습니다/)).toBeTruthy();
    expect(within(field).getByText(/수락해 담당자가 된 뒤/)).toBeTruthy();
    expect(within(field).queryByLabelText("파일 추가")).toBeNull();
  });

  it("관리자 배정 갈래에서는 그 담당자가 붙인다고 말한다", async () => {
    renderModal({ assignCandidates: [jiho] });
    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "계약서 검토" } });
    fireEvent.change(screen.getByLabelText("담당자"), { target: { value: "jiho" } });

    const field = screen.getByLabelText("첨부파일");
    expect(within(field).getByText(/배정한 업무는 그 담당자입니다/)).toBeTruthy();
  });

  /** **조용히 버리지 않는다** — 고른 파일은 남고, 왜 함께 못 가는지가 화면에 선다. */
  it("파일을 고른 뒤 담당을 남으로 바꾸면 그 파일을 버리지 않고 못 붙는다고 말한다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-9", title: "계약서 검토" } as never);
    const { onCreated } = renderModal({ assignCandidates: [jiho] });

    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "계약서 검토" } });
    pickFiles(["초안.pdf"]);
    fireEvent.change(screen.getByLabelText("담당자"), { target: { value: "jiho" } });

    const field = screen.getByLabelText("첨부파일");
    expect(within(field).getByText(/이 경로로는 함께 붙지 않습니다/)).toBeTruthy();
    expect(within(within(field).getByLabelText("함께 붙지 않는 파일")).getByText("초안.pdf")).toBeTruthy();

    // 담당을 나로 되돌리면 고른 파일이 그대로 다시 선다.
    fireEvent.change(screen.getByLabelText("담당자"), { target: { value: "me" } });
    expect(within(screen.getByLabelText("첨부할 파일")).getByText("초안.pdf")).toBeTruthy();

    // 그리고 그 갈래로 보내도 업로드를 «성공한 척» 부르지 않는다.
    fireEvent.change(screen.getByLabelText("담당자"), { target: { value: "jiho" } });
    fireEvent.click(screen.getByRole("button", { name: "업무 배정" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(api.uploadTaskMaterial).not.toHaveBeenCalled();
  });
});
