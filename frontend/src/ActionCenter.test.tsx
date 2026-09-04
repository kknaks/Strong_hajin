import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ActionItemDetail } from "./viewModels";

vi.mock("./api", () => ({ getActionItem: vi.fn(), runActionCommand: vi.fn() }));

import * as api from "./api";
import { ActionItemCard, ActionItemDrawer } from "./ActionCenter";

const personas = [
  { id: "mina", display_name: "민아 (구성원)" },
  { id: "jiho", display_name: "지호 (팀장)" },
];

const adjusted: ActionItemDetail = {
  action_item_id: "item-1",
  kind: "work_request.acceptance",
  status: "awaiting_revision",
  subject: "견적 재검토",
  operation_label: "업무 요청",
  current_question: "조정 요청에 답해 수정안을 다시 보낼지 결정하세요",
  preview: [{ id: "requester", label: "요청자", value: "민아 (구성원)", kind: "person" }],
  allowed_commands: [
    { id: "revise", label: "수정안 재상신", tone: "primary", requires_reason: false },
    { id: "withdraw", label: "요청 철회", tone: "neutral", requires_reason: false },
  ],
  submission_version: 1,
  waiting_on: { member_id: "mina", display_name: "민아 (구성원)" },
  resource: { type: "work_request", id: "request-1" },
  expected_version: 2,
  rounds: [
    {
      submission_id: "s1",
      submission_version: 1,
      submitted_by: "mina",
      submitted_at: "2026-09-03T01:00:00Z",
      content_hash: "hash-1",
      snapshot: { title: "견적 재검토", description: "처음 설명", due_date: "2026-09-20" },
      diff: null,
      decisions: [
        { review_decision_id: "d1", actor_member_id: "jiho", decision: "negotiate", reason: "기한을 늦춰 주세요", decided_at: "2026-09-03T02:00:00Z" },
      ],
    },
  ],
};

function renderDrawer(detail: ActionItemDetail, onDone = vi.fn().mockResolvedValue(true)) {
  vi.mocked(api.getActionItem).mockResolvedValue(detail);
  vi.mocked(api.runActionCommand).mockResolvedValue({} as never);
  const onNotice = vi.fn();
  const onClose = vi.fn();
  render(
    <ActionItemDrawer
      actionItemId={detail.action_item_id}
      onClose={onClose}
      onDone={onDone}
      onError={vi.fn()}
      onNotice={onNotice}
      personas={personas}
    />,
  );
  return { onNotice, onClose, onDone };
}

describe("judgement card", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("labels an adjustment as 조정 필요 and names whose turn it is, from the server projection alone", () => {
    const { container } = render(<ActionItemCard item={adjusted} onOpen={vi.fn()} personas={personas} />);
    const card = container.querySelector(".task-card") as HTMLElement;
    expect(card.getAttribute("data-kind")).toBe("work_request.acceptance");
    expect(within(card).getByText("조정 필요")).toBeTruthy();
    expect(within(card).getByText("업무 요청")).toBeTruthy(); // the server's operation label, not the kind
    expect(within(card).getByText("견적 재검토")).toBeTruthy();
    expect(within(card).getByText("조정 요청에 답해 수정안을 다시 보낼지 결정하세요")).toBeTruthy();
    expect(within(card).getByText("민아 차례")).toBeTruthy();
  });
});

describe("adjustment and resubmission", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("opens on the previous adjustment reason and offers only the commands the server allowed", async () => {
    renderDrawer(adjusted);
    const drawer = await screen.findByRole("dialog", { name: "판단 상세" });
    expect(within(drawer).getByText("조정 요청에 답해 수정안을 다시 보낼지 결정하세요")).toBeTruthy();
    // The reason that sent it back is visible on first open, in the round history.
    expect(within(drawer).getByText(/기한을 늦춰 주세요/)).toBeTruthy();
    expect(within(drawer).getByText(/지호/)).toBeTruthy();
    expect(within(drawer).getByRole("button", { name: "수정안 재상신" })).toBeTruthy();
    expect(within(drawer).getByRole("button", { name: "요청 철회" })).toBeTruthy();
    expect(within(drawer).queryByRole("button", { name: "수락" })).toBeNull();
  });

  it("prefills the revision form, shows the diff before sending, and refuses a revision that changes nothing", async () => {
    renderDrawer(adjusted);
    const drawer = await screen.findByRole("dialog", { name: "판단 상세" });
    fireEvent.click(within(drawer).getByRole("button", { name: "수정안 재상신" }));

    const title = (await within(drawer).findByLabelText("요청할 업무")) as HTMLInputElement;
    expect(title.value).toBe("견적 재검토"); // prefilled with the round being revised
    expect((within(drawer).getByLabelText("요청 내용") as HTMLTextAreaElement).value).toBe("처음 설명");
    const submit = within(drawer).getByRole("button", { name: "수정안 재상신" });
    expect(submit.hasAttribute("disabled")).toBe(true); // nothing changed yet
    expect(within(drawer).getByText(/바뀐 내용이 없습니다/)).toBeTruthy();

    fireEvent.change(title, { target: { value: "견적 재검토 (기한 조정)" } });
    const summary = within(drawer).getByLabelText("제출 전 변경 요약");
    expect(within(summary).getByText(/견적 재검토 \(기한 조정\)/)).toBeTruthy();
    // A changed due date in the pre-submit summary is formatted too, never raw ISO.
    fireEvent.change(within(drawer).getByLabelText("희망 기한"), { target: { value: "2026-10-15" } });
    expect(within(summary).getByText("2026/09/20")).toBeTruthy();
    expect(within(summary).getByText("2026/10/15")).toBeTruthy();
    expect(summary.textContent).not.toMatch(/2026-\d\d-\d\d/);
    expect(submit.hasAttribute("disabled")).toBe(false);

    fireEvent.click(submit);
    await waitFor(() => expect(api.runActionCommand).toHaveBeenCalled());
    // Only the fields that actually changed are sent, against the version the server handed out.
    expect(vi.mocked(api.runActionCommand).mock.calls[0]).toEqual([
      "item-1",
      "revise",
      { expected_version: 2, changes: { title: "견적 재검토 (기한 조정)", due_date: "2026-10-15" } },
    ]);
  });

  it("asks for a reason exactly when the server said the command needs one", async () => {
    renderDrawer({
      ...adjusted,
      status: "awaiting_review",
      current_question: "이 업무 요청을 수락할지 결정하세요",
      allowed_commands: [
        { id: "accept", label: "수락", tone: "primary", requires_reason: false },
        { id: "adjust", label: "조정 요청", tone: "neutral", requires_reason: true },
      ],
    });
    const drawer = await screen.findByRole("dialog", { name: "판단 상세" });

    fireEvent.click(within(drawer).getByRole("button", { name: "조정 요청" }));
    const reason = await within(drawer).findByLabelText("조정 요청 사유");
    expect(within(drawer).getByRole("button", { name: "조정 요청 확정" }).hasAttribute("disabled")).toBe(true);
    fireEvent.change(reason, { target: { value: "기한을 늦춰 주세요" } });
    fireEvent.click(within(drawer).getByRole("button", { name: "조정 요청 확정" }));

    await waitFor(() => expect(api.runActionCommand).toHaveBeenCalled());
    expect(vi.mocked(api.runActionCommand).mock.calls[0]).toEqual([
      "item-1",
      "adjust",
      { expected_version: 2, reason: "기한을 늦춰 주세요" },
    ]);
  });

  it("claims the judgement is reflected only when every projection settled", async () => {
    const { onNotice } = renderDrawer({ ...adjusted, status: "awaiting_review", allowed_commands: [{ id: "accept", label: "수락", tone: "primary", requires_reason: false }] }, vi.fn().mockResolvedValue(false));
    const drawer = await screen.findByRole("dialog", { name: "판단 상세" });
    fireEvent.click(within(drawer).getByRole("button", { name: "수락" }));
    await waitFor(() => expect(onNotice).toHaveBeenCalled());
    expect(onNotice).toHaveBeenCalledWith("'견적 재검토' 판단을 저장했습니다.");
  });

  it("shows every earlier round with its diff and decision, marked as unchangeable", async () => {
    renderDrawer({
      ...adjusted,
      status: "awaiting_review",
      submission_version: 2,
      allowed_commands: [],
      rounds: [
        ...adjusted.rounds,
        {
          submission_id: "s2",
          submission_version: 2,
          submitted_by: "mina",
          submitted_at: "2026-09-04T01:00:00Z",
          content_hash: "hash-2",
          snapshot: { title: "견적 재검토 (기한 조정)", description: "처음 설명", due_date: "2026-09-30" },
          diff: { title: { before: "견적 재검토", after: "견적 재검토 (기한 조정)" }, due_date: { before: "2026-09-20", after: "2026-09-30" } },
          decisions: [],
        },
      ],
    });
    const drawer = await screen.findByRole("dialog", { name: "판단 상세" });
    const history = within(drawer).getByLabelText("회차 기록");
    expect(within(history).getByText(/이전 회차는 수정되지 않습니다/)).toBeTruthy();
    expect(history.querySelectorAll(".round-row")).toHaveLength(2);
    const second = history.querySelector('[data-submission-version="2"]') as HTMLElement;
    // Each round carries its own frozen content, and the diff still names what it replaced.
    expect(within(second.querySelector(".round-snapshot") as HTMLElement).getByText("견적 재검토 (기한 조정)")).toBeTruthy();
    // Read-only dates are YYYY/MM/DD everywhere, including inside a diff.
    const diff = second.querySelector(".round-diff") as HTMLElement;
    expect(within(diff).getByText("2026/09/20")).toBeTruthy();
    expect(within(diff).getByText("2026/09/30")).toBeTruthy();
    expect(diff.textContent).not.toMatch(/2026-09-\d\d/);
    expect((second.querySelector(".round-snapshot") as HTMLElement).textContent).not.toMatch(/2026-09-\d\d/);
    const first = history.querySelector('[data-submission-version="1"]') as HTMLElement;
    expect(within(first.querySelector(".round-snapshot") as HTMLElement).getByText("견적 재검토")).toBeTruthy();
    expect(within(first.querySelector(".round-snapshot") as HTMLElement).getByText("처음 설명")).toBeTruthy();
    expect(within(first).getByText(/조정 요청 · 기한을 늦춰 주세요/)).toBeTruthy();
  });
});
