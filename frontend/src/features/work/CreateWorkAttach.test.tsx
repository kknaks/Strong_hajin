import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", () => ({
  createDirectTask: vi.fn(),
  createWorkRequest: vi.fn(),
  assignTask: vi.fn(),
  uploadTaskMaterial: vi.fn(),
  uploadWorkRequestMaterial: vi.fn(),
  attachWorkRequestMaterialLink: vi.fn(),
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

/** 최종 발주 2: 자료는 왼쪽 세로 탭의 「자료」 판에 산다 — 필드는 그대로이고 자리만 옮겼다. */
const openMaterials = () => fireEvent.click(screen.getByRole("tab", { name: "자료" }));

function pickFiles(names: string[]) {
  openMaterials();
  const input = screen.getByLabelText("파일 추가") as HTMLInputElement;
  fireEvent.change(input, { target: { files: names.map(file) } });
}

/** 링크 자료 한 줄을 더한다 — 주소와 사람이 읽는 이름 둘 다 있어야 「링크 추가」가 열린다. */
function addLink(url: string, label: string) {
  openMaterials();
  fireEvent.change(screen.getByLabelText("링크 주소"), { target: { value: url } });
  fireEvent.change(screen.getByLabelText("링크 이름"), { target: { value: label } });
  fireEvent.click(screen.getByRole("button", { name: "링크 추가" }));
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

describe("요청 갈래의 자료 — 보낸 요청에 두 단계로 붙는다", () => {
  /**
   * **요청 자료는 «생성 payload» 가 아니라 «두 번째 걸음» 이다** (WORK-003).
   *
   * 한때 이 자리에 「저장되지 않습니다」가 서 있었다 — 요청에 자료를 싣는 계약이 없어, 고른 파일과
   * 링크가 어디로도 가지 않던 시절이다. 그 계약이 열리면서(`POST /api/work-requests/{id}/materials`
   * 와 `…/materials/links`) 요청도 내 업무와 **같은 걸음**을 걷는다: 보내고 → 돌아온 `request_id`
   * 로 붙인다.
   *
   * 그래서 이 묶음이 못박는 것은 셋이다: ① 생성 payload 에 자료 키가 **여전히 없다**, ② 돌아온
   * `request_id` 로 파일과 링크가 **실제로 올라간다**, ③ 한 건이 실패해도 **요청은 살아 있고**
   * 그 건만 다시 시도한다.
   */
  const sendRequest = async () => {
    fireEvent.click(screen.getByRole("tab", { name: "기본 정보" }));
    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "부탁한 업무" } });
    fireEvent.click(screen.getByLabelText("담당 후보"));
    fireEvent.click(await screen.findByRole("option", { name: "소라 (기획)" }));
    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));
  };

  it("요청 갈래에도 자료 탭과 파일·링크 입력이 선다", () => {
    renderModal({ canCreateTask: false });
    expect(screen.getByRole("tab", { name: "자료" })).toBeTruthy();
    openMaterials();
    expect(screen.getByLabelText("첨부파일")).toBeTruthy();
    expect(screen.getByLabelText("파일 추가")).toBeTruthy();
    expect(screen.getByLabelText("링크 주소")).toBeTruthy();
    expect(screen.getByLabelText("링크 이름")).toBeTruthy();
  });

  it("「저장되지 않습니다」도 그 경고도 서지 않는다 — 실제로 붙는 갈래다", () => {
    renderModal({ canCreateTask: false });
    pickFiles(["초안.pdf"]);
    addLink("https://wiki.example/spec", "기획 위키");

    const field = screen.getByLabelText("첨부파일");
    expect(within(field).queryByRole("alert")).toBeNull();
    expect(field.textContent).not.toContain("요청에 자료를 싣는 API가 아직 없습니다");
    expect(within(screen.getByLabelText("첨부할 파일")).queryByText("저장되지 않습니다")).toBeNull();
    expect(within(screen.getByLabelText("첨부할 링크")).queryByText("저장되지 않습니다")).toBeNull();
    // 고른 것을 화면에서 지우지 않는다 — 빼는 것은 사람이 한다
    expect(screen.getByRole("button", { name: "기획 위키 빼기" })).toBeTruthy();
  });

  /**
   * **임의 payload 를 지어내지 않는다.**
   *
   * `material_ids`·`attachments`·`material_draft_ids` 같은 이름을 서버가 모르는 채로 실으면 422
   * 이거나 조용히 버려지는데, 둘 다 화면에서는 「저장됐다」로 읽힌다. 댓글 첨부·evidence 입구로
   * 우회하지도 않는다 — 뜻이 다른 자리다.
   */
  it("요청 생성 payload 에는 자료 키가 실리지 않는다 — 우회 입구로도 새지 않는다", async () => {
    vi.mocked(api.createWorkRequest).mockResolvedValue({ request_id: "req-7", title: "부탁한 업무" } as never);
    vi.mocked(api.uploadWorkRequestMaterial).mockResolvedValue({} as never);
    vi.mocked(api.attachWorkRequestMaterialLink).mockResolvedValue({} as never);
    renderModal({ canCreateTask: false });
    pickFiles(["초안.pdf"]);
    addLink("https://wiki.example/spec", "기획 위키");
    await sendRequest();

    await waitFor(() => expect(api.createWorkRequest).toHaveBeenCalled());
    const payload = JSON.stringify(vi.mocked(api.createWorkRequest).mock.calls[0][2]);
    expect(payload).not.toContain("material");
    expect(payload).not.toContain("attachment");
    expect(payload).not.toContain("draft");
    expect(payload).not.toContain("wiki.example");
    // 업무 자료 입구로도, 댓글·evidence 우회로도 새지 않는다
    expect(api.uploadTaskMaterial).not.toHaveBeenCalled();
    expect(api.attachTaskMaterialLink).not.toHaveBeenCalled();
    expect(api.uploadCommentAttachment).not.toHaveBeenCalled();
    expect(api.uploadRequestEvidence).not.toHaveBeenCalled();
  });

  it("요청을 보낸 뒤 돌아온 request_id 로 파일을 올린다 — 새 payload 키 없이 두 단계다", async () => {
    vi.mocked(api.createWorkRequest).mockResolvedValue({ request_id: "req-7", title: "부탁한 업무" } as never);
    vi.mocked(api.uploadWorkRequestMaterial).mockResolvedValue({} as never);
    const { onCreated, onClose } = renderModal({ canCreateTask: false });
    pickFiles(["초안.pdf", "의견.md"]);
    await sendRequest();

    await waitFor(() => expect(api.uploadWorkRequestMaterial).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.uploadWorkRequestMaterial).mock.calls.map((call) => call[0])).toEqual(["req-7", "req-7"]);
    expect(vi.mocked(api.uploadWorkRequestMaterial).mock.calls.map((call) => (call[1] as File).name)).toEqual([
      "초안.pdf",
      "의견.md",
    ]);
    await waitFor(() =>
      expect(vi.mocked(onCreated).mock.calls.at(-1)).toEqual([
        "'부탁한 업무' 업무를 소라에게 보냈습니다. 상대가 수락하면 그 사람의 업무가 됩니다. 자료 2건을 함께 보냈습니다.",
        { assignedToOther: true },
      ]),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("링크도 같은 두 단계다 — 보낸 뒤 그 request_id 로 붙는다", async () => {
    vi.mocked(api.createWorkRequest).mockResolvedValue({ request_id: "req-7", title: "부탁한 업무" } as never);
    vi.mocked(api.attachWorkRequestMaterialLink).mockResolvedValue({} as never);
    const { onClose } = renderModal({ canCreateTask: false });
    addLink("https://wiki.example/spec", "기획 위키");
    await sendRequest();

    await waitFor(() => expect(api.attachWorkRequestMaterialLink).toHaveBeenCalled());
    expect(vi.mocked(api.attachWorkRequestMaterialLink).mock.calls[0]).toEqual([
      "req-7",
      { url: "https://wiki.example/spec", label: "기획 위키" },
    ]);
    // 업무 자료 링크 입구로 새지 않는다
    expect(api.attachTaskMaterialLink).not.toHaveBeenCalled();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  /**
   * **요청은 이미 갔다.** 남은 것은 자료뿐이라, 여기서 다시 누르는 것은 «업로드 재시도» 이지
   * 「처음부터 다시」가 아니다 — 그러면 같은 요청이 둘 간다.
   */
  it("자료 한 건이 실패하면 요청은 살리고 그 건만 다시 시도한다", async () => {
    vi.mocked(api.createWorkRequest).mockResolvedValue({ request_id: "req-7", title: "부탁한 업무" } as never);
    vi.mocked(api.uploadWorkRequestMaterial).mockResolvedValue({} as never);
    vi.mocked(api.attachWorkRequestMaterialLink)
      .mockRejectedValueOnce(new Error("서버 오류"))
      .mockResolvedValueOnce({} as never);
    const { onCreated, onError, onClose } = renderModal({ canCreateTask: false });
    pickFiles(["초안.pdf"]);
    addLink("https://wiki.example/spec", "기획 위키");
    await sendRequest();

    // 보냈다는 사실은 먼저 알린다 — 목록이 그 요청을 들고 있어야 한다.
    await waitFor(() =>
      expect(vi.mocked(onCreated).mock.calls.at(-1)).toEqual([
        "'부탁한 업무' 업무를 소라에게 보냈습니다. 상대가 수락하면 그 사람의 업무가 됩니다.",
        { assignedToOther: true },
      ]),
    );
    // 그러나 닫지 않는다: 자료가 남았다는 사실을 사람이 보고 골라야 한다.
    expect(onClose).not.toHaveBeenCalled();
    expect(vi.mocked(onError).mock.calls.at(-1)?.[0]).toContain("요청은 보냈지만 첨부 1건을 올리지 못했습니다");

    fireEvent.click(await screen.findByRole("button", { name: "첨부 다시 시도 (1)" }));

    await waitFor(() => expect(api.attachWorkRequestMaterialLink).toHaveBeenCalledTimes(2));
    // **발송은 여전히 한 번이고**, 성공한 파일을 다시 올리지도 않는다.
    expect(api.createWorkRequest).toHaveBeenCalledTimes(1);
    expect(api.uploadWorkRequestMaterial).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  /* 최종 발주 3: 「담당을 남으로 바꾸면」 갈래들이 여기 있었다. 업무 갈래에서 담당자 칸이 사라져
     그 전환을 화면에서 만들 길이 없다 — 부를 수 없는 것을 부르는 척하는 검사를 남기지 않는다. */
});

describe("내 업무의 링크 자료 — 파일과 같은 두 단계다", () => {
  it("업무를 만든 뒤 그 task_id 로 링크를 붙인다 — 새 서버 API 없이 두 단계다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-9", title: "계약서 검토" } as never);
    vi.mocked(api.attachTaskMaterialLink).mockResolvedValue({} as never);
    const { onCreated } = renderModal();

    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "계약서 검토" } });
    addLink("https://wiki.example/spec", "기획 위키");
    // 내 업무에서는 「저장되지 않습니다」가 붙지 않는다 — 실제로 붙는 갈래다
    expect(within(screen.getByLabelText("첨부할 링크")).queryByText("저장되지 않습니다")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    await waitFor(() => expect(api.attachTaskMaterialLink).toHaveBeenCalled());
    expect(vi.mocked(api.attachTaskMaterialLink).mock.calls[0]).toEqual([
      "task-9",
      "input",
      { url: "https://wiki.example/spec", label: "기획 위키" },
    ]);
    await waitFor(() =>
      expect(onCreated).toHaveBeenCalledWith("'계약서 검토' 업무를 만들었습니다. 첨부 1건을 올렸습니다.", { assignedToOther: false }),
    );
  });

  it("주소만 적고 이름이 없으면 더하지 못한다 — 목록에 주소가 그대로 설 자리를 만들지 않는다", () => {
    renderModal();
    openMaterials();
    fireEvent.change(screen.getByLabelText("링크 주소"), { target: { value: "https://wiki.example/spec" } });
    expect((screen.getByRole("button", { name: "링크 추가" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("링크 이름"), { target: { value: "기획 위키" } });
    expect((screen.getByRole("button", { name: "링크 추가" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("파일이 붙고 링크가 실패하면 그 링크만 다시 시도한다 — 업무도 파일도 다시 붙이지 않는다", async () => {
    vi.mocked(api.createDirectTask).mockResolvedValue({ task_id: "task-9", title: "계약서 검토" } as never);
    vi.mocked(api.uploadTaskMaterial).mockResolvedValue({} as never);
    vi.mocked(api.attachTaskMaterialLink).mockRejectedValueOnce(new Error("서버 오류")).mockResolvedValueOnce({} as never);
    const { onError, onClose } = renderModal();

    fireEvent.change(screen.getByLabelText("업무 제목"), { target: { value: "계약서 검토" } });
    pickFiles(["초안.pdf"]);
    addLink("https://wiki.example/spec", "기획 위키");
    fireEvent.click(screen.getByRole("button", { name: "업무 추가" }));

    const retry = await screen.findByRole("button", { name: "첨부 다시 시도 (1)" });
    expect(vi.mocked(onError).mock.calls.at(-1)?.[0]).toContain("첨부 1건을 올리지 못했습니다");
    fireEvent.click(retry);

    await waitFor(() => expect(api.attachTaskMaterialLink).toHaveBeenCalledTimes(2));
    // 성공한 파일을 다시 올리지 않고, 업무를 다시 만들지도 않는다
    expect(api.uploadTaskMaterial).toHaveBeenCalledTimes(1);
    expect(api.createDirectTask).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});
