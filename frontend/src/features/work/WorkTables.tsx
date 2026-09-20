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
 * 시안의 표 네 벌 (WORK-002 Phase 7-A·7-B·7-C · 4차 발주 2).
 *
 * 네 표가 같은 격자(`.scax-task-table`)를 쓰고 **칸의 뜻만 다르다** — 내 업무는 「요청자·상태·액션」,
 * 보낸 업무는 「담당자·요청 상태·다시 요청」, 완료 업무는 「상대·처리일·완료/취소」,
 * 참조 업무는 「보낸 사람 → 담당자·요청 상태·상세보기」다.
 * 네 상태(default/empty/loading/error)는 `TableState` 하나가 네 표에 같은 모양으로 낸다.
 *
 * **4차 발주 2**: 격자를 «말로» 같게 두는 것으로는 어긋남이 계속 났다 — 표마다 골격 마크업을 따로
 * 써서 머리 한 줄, 행 한 줄이 각자 손으로 쓰였고, 완료 표는 행이 그룹 `<div>` 안에 들어가 `--nostar`
 * 의 자식 선택자에서 빠져 **첫 트랙이 24px 살아 있었다**(제목 시작 위치가 혼자 밀렸다). 그래서
 * 골격을 **부품 하나로 모았다** — `WorkTableShell` 이 머리·본문을, `WorkTableRow` 가 행을 낸다.
 * 이제 열 폭·행 높이·padding·머리는 CSS 한 줄이 네 표에 같이 걸린다.
 *
 * **별표 열은 없다** — `starred` 필드도 저장 경로도 없어서 눌러도 아무 데도 안 남는다(M-21).
 * 그래서 네 표 모두 `--nostar` 로 첫 트랙을 지운다. 필드가 생기면 modifier 만 뗀다.
 */

export type TableState = "loading" | "error" | "ready";

/** 네 표가 같이 쓰는 머리 다섯 칸. 첫 칸은 제목(왼쪽 정렬), 나머지는 가운데다. */
export type WorkTableHeaders = readonly [string, string, string, string, string];

/**
 * 표 골격 — 머리 한 줄과 스크롤하는 본문 (4차 발주 2).
 *
 * 부르는 쪽은 **칸의 뜻**(머리 글자)과 **행**만 준다. 격자·높이·padding 은 여기 한 자리다.
 */
export function WorkTableShell({
  label,
  headers,
  children,
}: {
  label: string;
  headers: WorkTableHeaders;
  children: React.ReactNode;
}) {
  return (
    <div aria-label={label} className="scax-task-table scax-task-table--nostar" role="table">
      <div className="scax-task-table__head" role="row">
        {headers.map((header, index) => (
          <span className={index === 0 ? undefined : "scax-task-table__cell--center"} key={`${index}-${header}`} role="columnheader">
            {header}
          </span>
        ))}
      </div>
      <div className="scax-task-table__body">{children}</div>
    </div>
  );
}

/**
 * 표의 한 줄 — 누르면 열린다.
 *
 * 줄 안의 단추·입력은 «줄을 여는» 클릭이 아니다. 그 판정도 여기 한 자리에 둔다 — 표마다 손으로 쓰면
 * 한 표에서만 빠져 단추를 눌렀는데 상세가 열리는 일이 생긴다.
 */
export function WorkTableRow({
  onOpen,
  children,
  ...rest
}: {
  onOpen: () => void;
  children: React.ReactNode;
} & Omit<React.HTMLAttributes<HTMLDivElement>, "onClick" | "className" | "role" | "children">) {
  return (
    <div
      {...rest}
      className="scax-task-table__row openable"
      onClick={(event) => {
        if ((event.target as HTMLElement).closest("button, input, select, textarea, a, label")) return;
        onOpen();
      }}
      role="row"
    >
      {children}
    </div>
  );
}

/** 가운데 정렬 칸 하나. `muted` 는 이름·날짜처럼 읽기만 하는 값이다. */
export function WorkTableCell({ muted = false, children }: { muted?: boolean; children: React.ReactNode }) {
  return (
    <span className={muted ? "scax-task-table__cell--center scax-task-table__cell--muted" : "scax-task-table__cell--center"} role="cell">
      {children}
    </span>
  );
}

/** 액션 칸 — 네 표가 같은 폭·같은 간격으로 쓴다. */
export function WorkTableActions({ children }: { children: React.ReactNode }) {
  return (
    <span className="scax-task-table__cell--actions" role="cell">
      {children}
    </span>
  );
}

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
    <WorkTableShell headers={["업무명", "요청자", "기한", "상태", "액션"]} label="내 업무">
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
          <WorkTableRow
            data-awaiting-acceptance={row.awaitingAcceptance ? "true" : undefined}
            data-task-row={row.id}
            key={row.id}
            onOpen={() => onOpen(row)}
          >
            <TitleCell cancelled={row.task?.state === "cancelled"} title={row.title}>
              <WaitingBadge task={row.task} />
              {row.task && <ChecklistCue progress={row.task.checklist_progress} />}
              {row.task?.block_reason && <small className="reason">막힘 사유: {row.task.block_reason}</small>}
            </TitleCell>
            <WorkTableCell muted>{requesterName(row)}</WorkTableCell>
            <WorkTableCell>
              <DueCell dueDate={row.dueDate} overdueDays={overdueDaysOf(row.task, today)} />
            </WorkTableCell>
            <WorkTableCell>{statusCell(row)}</WorkTableCell>
            <WorkTableActions>{actions(row)}</WorkTableActions>
          </WorkTableRow>
        ))}
    </WorkTableShell>
  );
}

/**
 * 제목 칸 — 네 표가 같은 자리에서 시작한다 (4차 발주 2).
 *
 * 들여쓰기도 별표 트랙도 없다. 완료 표의 그룹 아래 행도 **같은 왼쪽 선**에서 제목이 시작한다 —
 * 그룹 이름이 어디에 속한 행인지 이미 말하므로 제목을 더 밀 이유가 없다.
 */
function TitleCell({ title, cancelled, children }: { title: string; cancelled?: boolean; children?: React.ReactNode }) {
  return (
    <span className="scax-task-table__cell--title" role="cell">
      <span className="cell-main">
        <span className={cancelled ? "scax-task-table__title cancelled-title" : "scax-task-table__title"}>{title}</span>
        {children}
      </span>
    </span>
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
    <WorkTableShell headers={["제목", "담당자", "기한", "상태", "액션"]} label="보낸 업무">
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
          <WorkTableRow data-origin-kind={row.originKind} data-sent-row={row.id} key={row.id} onOpen={() => onOpen(row)}>
            <TitleCell title={row.title}>
              {/* 같은 표에 요청과 배정이 함께 서므로, 무엇으로 보낸 것인지 행이 말한다 (§2.1). */}
              <Badge tone="outline">{row.originKind === "work_request" ? "요청" : "배정"}</Badge>
            </TitleCell>
            <WorkTableCell muted>{row.assigneeName}</WorkTableCell>
            <WorkTableCell>
              <DueCell dueDate={row.dueDate} overdueDays={overdueDaysOf(row.task, today)} />
            </WorkTableCell>
            <WorkTableCell>
              {/* 시안의 배지 둘로는 요청 상태 여섯이 안 들어간다 — 여섯을 각각 낸다 (U-4). */}
              <Badge tone={sentToneToBadge[row.stateTone] ?? "neutral"}>{row.stateLabel}</Badge>
            </WorkTableCell>
            <WorkTableActions>{actions(row)}</WorkTableActions>
          </WorkTableRow>
        ))}
    </WorkTableShell>
  );
}

/* ---------------------------------------------------------------- 참조 업무 */

/** 참조로 받은 한 줄 — **읽는 자리다.** 수락·거절이 없고, 여는 상세도 읽기 전용이다 (4차 발주 1). */
export type CcRow = {
  id: string;
  title: string;
  /** 「보낸 사람 → 담당자」. 두 자리 중 어느 쪽도 내가 아니라서 둘 다 이름으로 선다. */
  counterpart: string;
  dueDate: string | null;
  stateLabel: string;
  stateTone: string;
  request: WorkRequest;
};

/**
 * 「참조 업무」 탭 (4차 발주 1).
 *
 * 지금까지 「보낸 업무」 안의 구획(`참조된 업무`)이던 목록을 **자기 탭으로** 냈다. 그 구획은 내가 보낸
 * 것이 아니라 **남이 나를 참조자로 넣은 것**이라 소유 축에서 다른 자리였고, 보낸 업무의 표와 다른
 * 골격(`<table>`)을 써서 제목 시작 위치도 혼자 달랐다.
 *
 * **판단하는 단추가 없다** — 참조자는 읽고 논의할 뿐이고, 수락·거절은 담당자의 자리다.
 */
export function CcTaskTable({
  rows,
  state,
  today,
  onOpen,
  onRetry,
  filtered,
  onClearFilter,
}: {
  rows: CcRow[];
  state: TableState;
  today: string;
  onOpen: (row: CcRow) => void;
  onRetry: () => void;
  filtered: boolean;
  onClearFilter: () => void;
}) {
  return (
    <WorkTableShell headers={["업무명", "보낸 사람 → 담당자", "기한", "상태", "액션"]} label="참조 업무">
      <TableStates
        emptyDescription="동료가 나를 참조자로 넣어 보낸 요청이 여기에 쌓입니다."
        emptyTitle="참조된 업무가 없습니다"
        errorTitle="참조 업무를 불러오지 못했습니다"
        filtered={filtered}
        onClearFilter={onClearFilter}
        onRetry={onRetry}
        rows={rows.length}
        state={state}
      />
      {state === "ready" &&
        rows.map((row) => (
          <WorkTableRow data-cc-row={row.id} key={row.id} onOpen={() => onOpen(row)}>
            <TitleCell title={row.title}>
              <Badge tone="outline">참조</Badge>
            </TitleCell>
            <WorkTableCell muted>{row.counterpart}</WorkTableCell>
            <WorkTableCell>
              <DueCell dueDate={row.dueDate} overdueDays={overdueDaysOf(null, today)} />
            </WorkTableCell>
            <WorkTableCell>
              <Badge tone={sentToneToBadge[row.stateTone] ?? "neutral"}>{row.stateLabel}</Badge>
            </WorkTableCell>
            <WorkTableActions>
              {/* 읽고 논의하는 자리 하나뿐이다 — 수락·거절은 담당자의 상세에만 선다. */}
              <Button onClick={() => onOpen(row)} size="sm" type="button" variant="text">
                상세보기
              </Button>
            </WorkTableActions>
          </WorkTableRow>
        ))}
    </WorkTableShell>
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
    <WorkTableShell headers={["제목", "상대", "처리일", "상태", "액션"]} label="완료 업무">
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
                <WorkTableRow data-done-row={row.id} key={row.id} onOpen={() => onOpen(row.task.task_id)}>
                  <TitleCell cancelled={row.task.state === "cancelled"} title={row.title} />
                  <WorkTableCell muted>{row.counterpart}</WorkTableCell>
                  <WorkTableCell muted>{row.closedAt ? formatDate(row.closedAt) : <EmptyValue />}</WorkTableCell>
                  <WorkTableCell>
                    <ClosedBadge task={row.task} />
                  </WorkTableCell>
                  <WorkTableActions>
                    {/* 재개는 상세에서만 부른다 — 봉투와 `derived.approval` 을 읽어야 하는 판단이라 행에 두지 않는다. */}
                    <Button onClick={() => onOpen(row.task.task_id)} size="sm" type="button" variant="text">
                      상세보기
                    </Button>
                  </WorkTableActions>
                </WorkTableRow>
              ))}
          </div>
        ))}
    </WorkTableShell>
  );
}

/* ---------------------------------------------------------------- 조직 업무 */

/**
 * 조직 업무 — 「보낸 업무」 탭 아래의 읽기 전용 구획.
 *
 * 4차 발주 2 로 **같은 골격**을 쓴다. 지금까지는 `<table>`(`DataTable`) 이라 같은 탭 안에서 열 폭도
 * 제목 시작 위치도 위의 표와 달랐다.
 */
export function OrganizationTaskTable({
  tasks,
  state,
  today,
  assigneeName,
  onOpen,
  onRetry,
}: {
  tasks: DirectTask[];
  state: TableState;
  today: string;
  assigneeName: (task: DirectTask) => string;
  onOpen: (task: DirectTask) => void;
  onRetry: () => void;
}) {
  return (
    <WorkTableShell headers={["업무명", "담당자", "기한", "상태", "액션"]} label="조직 업무">
      <TableStates
        emptyDescription="누군가 업무를 맡으면 여기에서 보입니다."
        emptyTitle="조직에 진행 중인 다른 업무가 없습니다"
        errorTitle="조직 업무를 불러오지 못했습니다"
        onRetry={onRetry}
        rows={tasks.length}
        state={state}
      />
      {state === "ready" &&
        tasks.map((task) => (
          <WorkTableRow data-organization-task={task.task_id} key={task.task_id} onOpen={() => onOpen(task)}>
            <TitleCell cancelled={task.state === "cancelled"} title={task.title} />
            <WorkTableCell muted>{assigneeName(task)}</WorkTableCell>
            <WorkTableCell>
              <DueCell dueDate={task.due_date ?? null} overdueDays={overdueDaysOf(task, today)} />
            </WorkTableCell>
            <WorkTableCell>{taskStateLabel[task.state] ?? task.state}</WorkTableCell>
            <WorkTableActions>
              <Button onClick={() => onOpen(task)} size="sm" type="button" variant="text">
                상세보기
              </Button>
            </WorkTableActions>
          </WorkTableRow>
        ))}
    </WorkTableShell>
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
