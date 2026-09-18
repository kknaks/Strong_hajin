import { useState } from "react";
import type React from "react";

import { Badge } from "../../ds/Badge";
import { Button } from "../../ds/Button";
import { Empty, EmptyValue } from "../../ds/Empty";
import { Icon } from "../../ds/icons/Icon";
import { Skeleton } from "../../ds/Skeleton";
import {
  cancelReasonLabel,
  derivedApprovalLabel,
  derivedAssignmentLabel,
  emptyActionLabel,
  formatDate,
  taskStateLabel,
  workRequestStateLabel,
  workRequestStateTone,
} from "../../lib/labels";
import type { DirectTask, WorkRequest } from "../../lib/viewModels";
import { approvalOf, assignmentOf, isRequestTask, overdueDaysOf, type WorkRow } from "./workRows";
import { ChecklistCue } from "./WorkViews";

/**
 * 시안의 표 세 벌 (WORK-002 Phase 7-A·7-B·7-C).
 *
 * 세 표가 같은 격자(`.scax-task-table`)를 쓰고 **칸의 뜻만 다르다** — 내 업무는 「요청자·상태·액션」,
 * 보낸 업무는 「담당자·요청 상태·다시 요청」, 완료 업무는 「상대·처리일·완료/취소」다.
 * 네 상태(default/empty/loading/error)는 `TableState` 하나가 세 표에 같은 모양으로 낸다.
 *
 * **별표 열은 없다** — `starred` 필드도 저장 경로도 없어서 눌러도 아무 데도 안 남는다(M-21).
 * 그래서 세 표 모두 `--nostar` 로 첫 트랙을 지운다. 필드가 생기면 modifier 만 뗀다.
 */

export type TableState = "loading" | "error" | "ready";

function TableStates({
  state,
  rows,
  emptyTitle,
  emptyDescription,
  errorTitle,
  onRetry,
  filtered,
  onClearFilter,
  emptyActionLabel: actionLabel,
  onEmptyAction,
}: {
  state: TableState;
  rows: number;
  emptyTitle: string;
  emptyDescription: string;
  errorTitle: string;
  onRetry: () => void;
  /** 비어 있는 이유가 «필터» 인가 — 「없다」와 「이 조건에 없다」는 다른 말이다. */
  filtered?: boolean;
  onClearFilter?: () => void;
  emptyActionLabel?: string;
  onEmptyAction?: () => void;
}) {
  if (state === "loading") return <Skeleton label={`${emptyTitle.replace(/가 없습니다$/, "")}을 불러오는 중`} rows={5} />;
  if (state === "error") {
    return (
      <Empty
        actionLabel={emptyActionLabel.error}
        description="네트워크 상태를 확인한 뒤 다시 시도해 주세요."
        onAction={onRetry}
        title={errorTitle}
        variant="error"
      />
    );
  }
  if (rows > 0) return null;
  if (filtered && onClearFilter) {
    return (
      <Empty
        actionLabel={emptyActionLabel.filter}
        description="다른 조건을 골라 보세요."
        onAction={onClearFilter}
        title="조건에 맞는 업무가 없습니다"
        variant="filter"
      />
    );
  }
  return actionLabel ? (
    <Empty actionLabel={actionLabel} description={emptyDescription} onAction={onEmptyAction} title={emptyTitle} />
  ) : (
    <Empty description={emptyDescription} title={emptyTitle} />
  );
}

/** 기한 한 칸 — 지난 만큼을 빨간 「+N」으로 덧붙인다. 상태는 바뀌지 않는다 (U-14). */
export function DueCell({ dueDate, overdueDays }: { dueDate: string | null; overdueDays: number }) {
  if (!dueDate) return <EmptyValue />;
  return (
    <span className="scax-task-table__due">
      {formatDate(dueDate)}
      {overdueDays > 0 && <span className="scax-task-table__due-extra">+{overdueDays}</span>}
    </span>
  );
}

/**
 * 한 행이 «무엇을 기다리는지» 를 한 낱말로 (F-4).
 *
 * **셋을 섞지 않는다** — 수락 대기 · 담당 변경 대기 · 완료 확인 대기는 각각 다른 값에서 나오고
 * 각각 다르게 읽혀야 한다. 값은 전부 서버가 낸 `derived` 다.
 */
export function WaitingBadge({ task }: { task: DirectTask | null }) {
  const assignment = assignmentOf(task);
  if (assignment) return <Badge tone={assignment === "awaiting_acceptance" ? "info" : "accent"}>{derivedAssignmentLabel[assignment]}</Badge>;
  const approval = approvalOf(task);
  if (approval === "awaiting_review") return <Badge tone="accent">{derivedApprovalLabel.awaiting_review}</Badge>;
  if (approval === "awaiting_revision") return <Badge tone="danger">{derivedApprovalLabel.awaiting_revision}</Badge>;
  const proposal = task?.derived?.proposal ?? null;
  if (proposal) return <Badge tone="danger">{proposal === "cancellation_pending" ? "취소 제안" : "조건 변경 제안"}</Badge>;
  return null;
}

/** 끝난 행이 「완료」인지 「취소 — 왜」인지 (F-3 · V-11). 취소는 완료가 아니다. */
export function ClosedBadge({ task }: { task: DirectTask }) {
  if (task.state === "cancelled") {
    const reason = task.cancel_reason ? cancelReasonLabel[String(task.cancel_reason)] : undefined;
    return <Badge tone="neutral">{reason ?? taskStateLabel.cancelled}</Badge>;
  }
  // 요청 업무의 승인 전 `done` 은 완결이 아니다 — 「확인 대기」가 그 자리다 (U-5).
  if (approvalOf(task) === "awaiting_review") return <Badge tone="accent">{derivedApprovalLabel.awaiting_review}</Badge>;
  return <Badge tone="positive">{taskStateLabel.done}</Badge>;
}

/* ---------------------------------------------------------------- 내 업무 */

export function TaskTable({
  rows,
  state,
  today,
  requesterName,
  onOpen,
  onRetry,
  filtered,
  onClearFilter,
  onCreate,
  statusCell,
  actions,
}: {
  rows: WorkRow[];
  state: TableState;
  today: string;
  requesterName: (row: WorkRow) => string;
  onOpen: (row: WorkRow) => void;
  onRetry: () => void;
  filtered: boolean;
  onClearFilter: () => void;
  onCreate?: () => void;
  /** 상태 칸에 들어가는 것 — 고칠 수 있으면 알약 Select, 아니면 읽기 글자다. */
  statusCell: (row: WorkRow) => React.ReactNode;
  /** 액션 칸 — 받은 요청이면 수락·거절, 내가 맡은 행이면 다음 한 걸음이다. */
  actions: (row: WorkRow) => React.ReactNode;
}) {
  return (
    <div aria-label="내 업무" className="scax-task-table scax-task-table--nostar" role="table">
      <div className="scax-task-table__head" role="row">
        <span role="columnheader">업무명</span>
        <span className="scax-task-table__cell--center" role="columnheader">요청자</span>
        <span className="scax-task-table__cell--center" role="columnheader">기한</span>
        <span className="scax-task-table__cell--center" role="columnheader">상태</span>
        <span className="scax-task-table__cell--center" role="columnheader">액션</span>
      </div>
      <div className="scax-task-table__body">
        <TableStates
          emptyActionLabel={onCreate ? "첫 업무 만들기" : undefined}
          emptyDescription="오늘 할 일을 등록하면 여기에 쌓입니다."
          emptyTitle="등록된 업무가 없습니다"
          errorTitle="업무 목록을 불러오지 못했습니다"
          filtered={filtered}
          onClearFilter={onClearFilter}
          onEmptyAction={onCreate}
          onRetry={onRetry}
          rows={rows.length}
          state={state}
        />
        {state === "ready" &&
          rows.map((row) => (
            <div
              className="scax-task-table__row openable"
              data-awaiting-acceptance={row.awaitingAcceptance ? "true" : undefined}
              data-task-row={row.id}
              key={row.id}
              onClick={(event) => {
                if ((event.target as HTMLElement).closest("button, input, select, textarea, a, label")) return;
                onOpen(row);
              }}
              role="row"
            >
              <span className="scax-task-table__cell--title" role="cell">
                <span className="cell-main">
                  <span className={row.task?.state === "cancelled" ? "scax-task-table__title cancelled-title" : "scax-task-table__title"}>
                    {row.title}
                  </span>
                  <WaitingBadge task={row.task} />
                  {row.task && <ChecklistCue progress={row.task.checklist_progress} />}
                  {row.task?.block_reason && <small className="reason">막힘 사유: {row.task.block_reason}</small>}
                </span>
              </span>
              <span className="scax-task-table__cell--center scax-task-table__cell--muted" role="cell">
                {requesterName(row)}
              </span>
              <span className="scax-task-table__cell--center" role="cell">
                <DueCell dueDate={row.dueDate} overdueDays={overdueDaysOf(row.task, today)} />
              </span>
              <span className="scax-task-table__cell--center" role="cell">
                {statusCell(row)}
              </span>
              <span className="scax-task-table__cell--actions" role="cell">
                {actions(row)}
              </span>
            </div>
          ))}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- 보낸 업무 */

/** 보낸 것 한 줄 — 요청이거나 배정이다. 둘은 **행에서 구분된다**(요청에만 수락 대기가 있다 · V-2·M-1). */
export type SentRow = {
  id: string;
  title: string;
  /** `work_request` 이면 수락 대기가 서고, `direct_assignment` 이면 서지 않는다. */
  originKind: "work_request" | "direct_assignment";
  assigneeName: string;
  dueDate: string | null;
  /** 요청이면 요청 상태 여섯 중 하나, 배정이면 담당 관계 상태다. */
  stateLabel: string;
  stateTone: string;
  request: WorkRequest | null;
  task: DirectTask | null;
  taskId: string | null;
};

const sentToneToBadge: Record<string, "accent" | "neutral" | "danger" | "positive" | "info"> = {
  warning: "info",
  accent: "accent",
  success: "positive",
  muted: "neutral",
};

export function SentTaskTable({
  rows,
  state,
  today,
  onOpen,
  onRetry,
  filtered,
  onClearFilter,
  actions,
}: {
  rows: SentRow[];
  state: TableState;
  today: string;
  onOpen: (row: SentRow) => void;
  onRetry: () => void;
  filtered: boolean;
  onClearFilter: () => void;
  actions: (row: SentRow) => React.ReactNode;
}) {
  return (
    <div aria-label="보낸 업무" className="scax-task-table scax-task-table--sent scax-task-table--nostar" role="table">
      <div className="scax-task-table__head" role="row">
        <span role="columnheader">제목</span>
        <span className="scax-task-table__cell--center" role="columnheader">담당자</span>
        <span className="scax-task-table__cell--center" role="columnheader">기한</span>
        <span className="scax-task-table__cell--center" role="columnheader">상태</span>
        <span className="scax-task-table__cell--center" role="columnheader">액션</span>
      </div>
      <div className="scax-task-table__body">
        <TableStates
          emptyDescription="업무를 만들어 담당자에게 요청해 보세요."
          emptyTitle="보낸 업무가 없습니다"
          errorTitle="보낸 업무를 불러오지 못했습니다"
          filtered={filtered}
          onClearFilter={onClearFilter}
          onRetry={onRetry}
          rows={rows.length}
          state={state}
        />
        {state === "ready" &&
          rows.map((row) => (
            <div
              className="scax-task-table__row openable"
              data-origin-kind={row.originKind}
              data-sent-row={row.id}
              key={row.id}
              onClick={(event) => {
                if ((event.target as HTMLElement).closest("button, input, select, textarea, a, label")) return;
                onOpen(row);
              }}
              role="row"
            >
              <span className="scax-task-table__cell--title" role="cell">
                <span className="cell-main">
                  <span className="scax-task-table__title">{row.title}</span>
                  {/* 같은 표에 요청과 배정이 함께 서므로, 무엇으로 보낸 것인지 행이 말한다 (§2.1). */}
                  <Badge tone="outline">{row.originKind === "work_request" ? "요청" : "배정"}</Badge>
                </span>
              </span>
              <span className="scax-task-table__cell--center scax-task-table__cell--muted" role="cell">
                {row.assigneeName}
              </span>
              <span className="scax-task-table__cell--center" role="cell">
                <DueCell dueDate={row.dueDate} overdueDays={overdueDaysOf(row.task, today)} />
              </span>
              <span className="scax-task-table__cell--center" role="cell">
                {/* 시안의 배지 둘로는 요청 상태 여섯이 안 들어간다 — 여섯을 각각 낸다 (U-4). */}
                <Badge tone={sentToneToBadge[row.stateTone] ?? "neutral"}>{row.stateLabel}</Badge>
              </span>
              <span className="scax-task-table__cell--actions" role="cell">
                {actions(row)}
              </span>
            </div>
          ))}
      </div>
    </div>
  );
}

/** 보낸 한 줄의 상태 낱말 — 요청이면 요청 상태 여섯, 배정이면 담당 관계다. */
export function sentStateOf(request: WorkRequest | null, assignmentStatus: string | null): { label: string; tone: string } {
  if (request) return { label: workRequestStateLabel[request.state] ?? request.state, tone: workRequestStateTone[request.state] ?? "muted" };
  if (assignmentStatus === "pending") return { label: "수락 대기", tone: "warning" };
  if (assignmentStatus === "active") return { label: "수락됨", tone: "success" };
  if (assignmentStatus === "declined") return { label: "거절됨", tone: "muted" };
  return { label: assignmentStatus ?? "—", tone: "muted" };
}

/* ---------------------------------------------------------------- 완료 업무 */

export type DoneGroup = {
  id: string;
  label: string;
  rows: Array<{ id: string; title: string; counterpart: string; closedAt: string | null; task: DirectTask }>;
};

export function DoneTaskTable({
  groups,
  state,
  onOpen,
  onRetry,
  filtered,
  onClearFilter,
}: {
  groups: DoneGroup[];
  state: TableState;
  onOpen: (taskId: string) => void;
  onRetry: () => void;
  filtered: boolean;
  onClearFilter: () => void;
}) {
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  const total = groups.reduce((sum, group) => sum + group.rows.length, 0);
  return (
    <div aria-label="완료 업무" className="scax-task-table scax-task-table--done scax-task-table--nostar" role="table">
      <div className="scax-task-table__head" role="row">
        <span role="columnheader">제목</span>
        <span className="scax-task-table__cell--center" role="columnheader">상대</span>
        <span className="scax-task-table__cell--center" role="columnheader">처리일</span>
        <span className="scax-task-table__cell--center" role="columnheader">상태</span>
        <span className="scax-task-table__cell--center" role="columnheader">액션</span>
      </div>
      <div className="scax-task-table__body">
        <TableStates
          emptyDescription="처리가 끝난 업무가 여기에 모입니다."
          emptyTitle="완료된 업무가 없습니다"
          errorTitle="완료 업무를 불러오지 못했습니다"
          filtered={filtered}
          onClearFilter={onClearFilter}
          onRetry={onRetry}
          rows={total}
          state={state}
        />
        {state === "ready" &&
          total > 0 &&
          groups.map((group) => (
            <div key={group.id}>
              <button
                aria-expanded={!closed[group.id]}
                className="scax-task-table__group"
                onClick={() => setClosed((current) => ({ ...current, [group.id]: !current[group.id] }))}
                type="button"
              >
                <Icon name={closed[group.id] ? "chevron-right" : "chevron-down"} size={16} />
                {group.label} ({group.rows.length})
              </button>
              {!closed[group.id] &&
                group.rows.map((row) => (
                  <div
                    className="scax-task-table__row openable"
                    data-done-row={row.id}
                    key={row.id}
                    onClick={(event) => {
                      if ((event.target as HTMLElement).closest("button, input, select, textarea, a, label")) return;
                      onOpen(row.task.task_id);
                    }}
                    role="row"
                  >
                    <span className="scax-task-table__cell--title scax-task-table__cell--indent" role="cell">
                      <span className={row.task.state === "cancelled" ? "scax-task-table__title cancelled-title" : "scax-task-table__title"}>
                        {row.title}
                      </span>
                    </span>
                    <span className="scax-task-table__cell--center scax-task-table__cell--muted" role="cell">
                      {row.counterpart}
                    </span>
                    <span className="scax-task-table__cell--center scax-task-table__cell--muted" role="cell">
                      {row.closedAt ? formatDate(row.closedAt) : <EmptyValue />}
                    </span>
                    <span className="scax-task-table__cell--center" role="cell">
                      <ClosedBadge task={row.task} />
                    </span>
                    <span className="scax-task-table__cell--actions" role="cell">
                      {/* 재개는 상세에서만 부른다 — 봉투와 `derived.approval` 을 읽어야 하는 판단이라 행에 두지 않는다. */}
                      <Button onClick={() => onOpen(row.task.task_id)} size="sm" type="button" variant="text">
                        상세보기
                      </Button>
                    </span>
                  </div>
                ))}
            </div>
          ))}
      </div>
    </div>
  );
}

/** 완료 업무 탭의 두 그룹 — 「내 업무」와 「보낸 업무」로 가른다 (시안). */
export function doneGroupsOf(
  mine: DirectTask[],
  sent: DirectTask[],
  counterpartOf: (task: DirectTask) => string,
): DoneGroup[] {
  const toRow = (task: DirectTask) => ({
    id: task.task_id,
    title: task.title,
    counterpart: counterpartOf(task),
    closedAt: task.completed_at ?? task.updated_at ?? null,
    task,
  });
  return [
    { id: "mine", label: "내 업무", rows: mine.map(toRow) },
    { id: "sent", label: "보낸 업무", rows: sent.map(toRow) },
  ].filter((group) => group.rows.length > 0);
}

export { isRequestTask };
