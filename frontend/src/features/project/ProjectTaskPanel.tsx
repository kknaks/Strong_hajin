import { useState } from "react";

import { Badge } from "../../ds/Badge";
import { Button } from "../../ds/Button";
import { Empty } from "../../ds/Empty";
import { Icon } from "../../ds/icons/Icon";
import { Select } from "../../ds/Select";
import { Skeleton } from "../../ds/Skeleton";
import { StatusNote } from "../../ds/StatusNote";
import { emptyActionLabel, personName, projectScreen, selectLabel, taskStateLabel, taskStateTone } from "../../lib/labels";
import type { DirectTask, ProjectTaskRow, TaskState } from "../../lib/viewModels";
/* 전이의 규칙·사유·완료 보고는 **업무 화면이 export 한 것을 가져다 쓴다** (D-32).
   ⚠ 그 파일을 고치면 업무 화면이 함께 움직인다 — 여기서는 **부르기만** 한다. */
import {
  allowedTaskTransitions,
  BlockReasonPrompt,
  CompletionReportModal,
  type TaskAction,
} from "../work/WorkModals";
import { isRequestTask } from "../work/workRows";
import { successorIndex, taskPercent, taskWhen } from "./projectModel";

/** 관계 줄 하나 — 읽을 수 없는 업무는 **자리가 남고 제목만 빈다** (SPEC-001 §4). */
type RelationRow = { taskId: string; title: string | null; state: TaskState | null };

function relationRows(ids: readonly string[], byId: Map<string, ProjectTaskRow>): RelationRow[] {
  return ids.map((taskId) => {
    const row = byId.get(taskId);
    return { taskId, title: row?.title ?? null, state: row?.state ?? null };
  });
}

/**
 * 우 레일 — 선택된 업무 하나의 상자 (WORK-005 FE-3).
 *
 * **선택된 하나에만 상세를 부른다** — 목록 전부를 상세로 긁으면 업무 수만큼 호출이 된다.
 * 체크리스트 «항목» 과 요청자는 그 상세에만 실리고, 그 밖의 사실(기간 · 담당 · 상태 · 관계 · 하위)은
 * 이미 프로젝트 응답이 싣고 있다 — 그래서 **상세가 아직 안 와도 상자는 선다.**
 *
 * **후행은 서버에 묻지 않는다** (D-07) — 그 프로젝트 업무 전부의 `preceding_task_ids` 를 뒤집어 만든다.
 *
 * **체크리스트는 읽기 전용이다** — 이 화면에서 항목을 끄고 켤 수 없다. 편집은 드로어에서만 된다.
 * ⚠ **쓰기 네 표면은 담당이 아니면 403 이 아니라 404 다** — 「눌러 보고 아는」 자리를 만들지 않는다.
 *
 * **블록 순서가 계약이다** (D-33, 사용자 결정): **메타 정보 → 업무 정보 → 관계 → 하위 업무.**
 * 먼저 읽어야 하는 것이 「무슨 일이고 어디까지 왔나」이고, 관계·하위는 그 다음이다.
 *
 * ⚠ **접근 값으로 체크리스트를 감추지 않는다** (D-29 · L-10). `access: "read_only"` 인데 항목이 실린
 * 응답이 **정상**이다 — 그 값은 이제 **쓰기 범위**만 뜻한다. 화면은 **「왔는가」로만** 그린다.
 */
export function ProjectTaskPanel({
  row,
  tasks,
  detail,
  detailState,
  onTask,
  onOpenTask,
  onTransition,
  onChanged,
  onError,
  onNotice,
  canManageOwnTasks = false,
  busy = false,
}: {
  row: ProjectTaskRow | null;
  tasks: ProjectTaskRow[];
  /** 선택된 업무의 상세. 아직 안 왔으면 `null` — 그래도 상자는 선다. */
  detail: DirectTask | null;
  detailState: "idle" | "loading" | "error" | "ready";
  onTask: (taskId: string) => void;
  onOpenTask: (taskId: string) => void;
  /** 상태를 바꾼다 — **업무 화면의 경로 그대로**다. 안 넘기면 상태 칸은 읽기 배지다. */
  onTransition?: (task: DirectTask, action: TaskAction, reason?: string) => Promise<boolean | void>;
  /** 완료 보고가 올라간 뒤 **다시 읽는** 자리 — 전이가 아닌 길로도 상태가 바뀐다. */
  onChanged?: () => Promise<void> | void;
  /**
   * 완료 보고 모달의 **유일한 오류 통로** — 그 모달은 자기 안에 오류를 그리지 않는다.
   * 빈 함수로 받으면 **서버가 거절해도 화면이 아무 말도 안 한다**(거절 갈래가 넷이다).
   */
  onError?: (message: string | null) => void;
  /** 「완료 보고를 보냈습니다」 — 선례가 `onError` 와 «둘을 함께» 넘긴다 (D-32). */
  onNotice?: (message: string) => void;
  /** 세션의 「자기 업무 관리」 역량 — **드롭다운 게이트 둘 중 하나**다 (D-32 ①-가). */
  canManageOwnTasks?: boolean;
  busy?: boolean;
}) {
  if (!row) {
    return (
      <section aria-label={projectScreen.sideEmptyTitle} className="scax-pj-side">
        {/* 글리프는 20px 그대로다 — 시안의 24px 과의 4px 차이로 `ds/Empty` 소비처 29곳을 흔들지 않는다 (D-24). */}
        <Empty description={projectScreen.sideEmptyDescription} icon="square-check" title={projectScreen.sideEmptyTitle} />
      </section>
    );
  }

  const byId = new Map(tasks.map((task) => [task.task_id, task]));
  const percent = taskPercent(row);
  const progress = row.checklist_progress;
  const predecessors = relationRows(row.preceding_task_ids ?? [], byId);
  const successors = relationRows(successorIndex(tasks).get(row.task_id) ?? [], byId);
  const children = tasks.filter((task) => task.parent_task_id === row.task_id);
  const parent = row.parent_task_id ? byId.get(row.parent_task_id) ?? null : null;
  const requester = detail?.origin?.actor ? personName(detail.origin.actor.display_name) : null;
  /**
   * 체크리스트 항목 — **키의 «유무»로 가른다** (D-29 · BE-3 실측).
   *
   * 안 실릴 때는 `null` 도 `[]` 도 아니고 **키 자체가 없다.** `detail.checklist.length` 로 읽으면
   * **프로젝트 밖 갈래에서 터진다.** 그 갈래는 이제 하나뿐이다 — **그 업무의 프로젝트에 안 붙은 사람.**
   * **접근 값은 여기에 쓰이지 않는다** — `read_only` 이면서 항목이 오는 조합이 정상이다 (L-10).
   */
  const checklist = detail?.checklist ?? null;

  return (
    <section aria-label={row.title} className="scax-pj-side" data-task-id={row.task_id}>
      <header className="scax-pj-side__head">
        <h2 className="scax-pj-side__title">{row.title}</h2>
        <div className="scax-pj-side__meter">
          <span className="scax-pj-side__track">
            {/* 체크리스트가 없으면 미터가 「—」다 — **0% 가 아니다** (D-02). */}
            {percent === null ? null : <span className="scax-pj-side__fill" style={{ width: `${percent}%` }} />}
          </span>
          <span className="scax-pj-side__pct">{percent === null ? "—" : `${percent}%`}</span>
        </div>
      </header>
      <div className="scax-pj-side__body">
        <section className="scax-pj-side__block">
          <h3 className="scax-pj-side__block-title">{projectScreen.metaTitle}</h3>
          {/* **「분류」가 없다** (D-08) — 담당이 그 자리에 선다. */}
          <dl className="scax-pj-facts">
            <dt className="scax-pj-facts__key">{projectScreen.metaState}</dt>
            <dd className="scax-pj-facts__val">
              <TaskStateValue
                busy={busy}
                canManageOwnTasks={canManageOwnTasks}
                detail={detail}
                onChanged={onChanged}
                onError={onError}
                onNotice={onNotice}
                onTransition={onTransition}
                row={row}
              />
            </dd>
            <dt className="scax-pj-facts__key">{projectScreen.metaWhen}</dt>
            <dd className="scax-pj-facts__val">{taskWhen(row)}</dd>
            {/* 담당이 없으면 그 칸을 «비운다» — 「미정」도 「—」도 지어내지 않는다 (I-1).
                좌 레일 카드·간트 이름줄·하위 카드가 이미 그렇게 낸다: 네 자리가 한 방식이다. */}
            {row.assignee && (
              <>
                <dt className="scax-pj-facts__key">{projectScreen.metaAssignee}</dt>
                <dd className="scax-pj-facts__val">{personName(row.assignee.display_name)}</dd>
              </>
            )}
            {requester && (
              <>
                <dt className="scax-pj-facts__key">{projectScreen.metaRequester}</dt>
                <dd className="scax-pj-facts__val">{requester}</dd>
              </>
            )}
            {parent && (
              <>
                <dt className="scax-pj-facts__key">{projectScreen.metaParent}</dt>
                <dd className="scax-pj-facts__val">
                  <button className="scax-pj-rel__item" onClick={() => onTask(parent.task_id)} type="button">
                    <span className="scax-pj-rel__name">{parent.title}</span>
                    <Icon name="chevron-right" size={12} />
                  </button>
                </dd>
              </>
            )}
          </dl>
        </section>

        {/* ② 업무 정보 — **설명과 체크리스트 둘을 한 블록이 든다** (D-33). 체크리스트가 「관계」 아래에
            홀로 서 있지 않다. 재료는 **선택된 하나의 업무 상세**이고 `tasks[]` 가 아니다 (D-38). */}
        <section className="scax-pj-side__block">
          <h3 className="scax-pj-side__block-title">{projectScreen.taskInfoTitle}</h3>
          {/* **설명은 네 갈래 전부에 온다** — 「읽기 전용이면 없겠지」는 틀리다 (L-13).
              비면 **그 자리를 비운다** — 「없음」도 「—」도 지어내지 않는다 (L-26). */}
          {detail?.description ? <p className="scax-pj-side__desc">{detail.description}</p> : null}

          <div className="scax-pj-rel">
            <span className="scax-pj-rel__head">
              <Icon name="square-check" size={14} />
              {projectScreen.checklistTitle}
              {/* 집계의 재료는 **프로젝트 상세 `tasks[]` 의 `checklist_progress`** 다 — 그쪽은 언제나 실린다.
                  업무 상세의 집계는 항목과 «함께» 빠지므로 미터를 그것으로 그리면 그 갈래에서 빈다. */}
              {progress && progress.total > 0 && (
                <span className="scax-pj-side__count">
                  {progress.done}/{progress.total}
                </span>
              )}
            </span>
            {detailState === "loading" && checklist === null ? (
              <Skeleton label={projectScreen.sideLoading} rows={2} />
            ) : detailState === "error" ? (
              <StatusNote tone="danger">{projectScreen.sideFailed}</StatusNote>
            ) : checklist && checklist.length > 0 ? (
              /* **읽기 전용이다** — `li` 와 `span` 뿐이고 누를 수 있는 것이 하나도 없다.
                 담당이 아니면 쓰기 네 표면이 **404** 라, 눌러 보고 아는 자리를 만들지 않는다. */
              <ul className="scax-pj-check">
                {checklist.map((item) => (
                  <li
                    className={`scax-pj-check__item${item.done ? " scax-pj-check__item--done" : ""}`}
                    key={item.item_id}
                  >
                    <span className="scax-pj-check__box">{item.done ? <Icon name="check" size={12} /> : null}</span>
                    <span className="scax-pj-check__text">{item.text}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="scax-pj-side__none">
                {progress && progress.total > 0 ? projectScreen.checklistHidden : projectScreen.checklistNone}
              </p>
            )}
          </div>
        </section>

        {/* ③ 관계 */}
        <section className="scax-pj-side__block">
          <h3 className="scax-pj-side__block-title">{projectScreen.relationTitle}</h3>
          <RelationBlock icon="arrow-up" label={projectScreen.predecessors} onTask={onTask} rows={predecessors} />
          <RelationBlock icon="arrow-down" label={projectScreen.successors} onTask={onTask} rows={successors} />
        </section>

        <section className="scax-pj-side__block">
          <h3 className="scax-pj-side__block-title">
            {projectScreen.childrenTitle}
            {children.length > 0 && <span className="scax-pj-side__count">{children.length}</span>}
          </h3>
          {children.length > 0 ? (
            <ul className="scax-pj-sub">
              {children.map((child) => {
                const childPercent = taskPercent(child);
                return (
                  <li key={child.task_id}>
                    <button className="scax-pj-sub__row" onClick={() => onTask(child.task_id)} type="button">
                      <span className="scax-pj-sub__top">
                        <span className="scax-pj-sub__name">{child.title}</span>
                        <Badge tone={taskStateTone[child.state] ?? "neutral"}>
                          {taskStateLabel[child.state] ?? child.state}
                        </Badge>
                      </span>
                      <span className="scax-pj-sub__meta">
                        {[
                          taskWhen(child),
                          child.assignee ? personName(child.assignee.display_name) : null,
                          childPercent === null ? null : `${childPercent}%`,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="scax-pj-side__none">{projectScreen.childrenNone}</p>
          )}
        </section>

        {/* **편집은 드로어에서만 된다** — 카드 클릭이 「선택」이 된 대신, 여는 길을 한 줄로 남긴다 (D-20). */}
        <Button onClick={() => onOpenTask(row.task_id)} size="sm" type="button" variant="outlined">
          {projectScreen.openTask}
        </Button>
      </div>
    </section>
  );
}

/**
 * 메타 표의 **「상태」 칸** — 내 업무면 드롭다운, 아니면 **읽기 배지** (D-32, 사용자 결정).
 *
 * ⚠ **「업무 화면 로직 그대로」는 부품 하나가 아니라 «그 화면이 상태를 바꿀 때 지나는 모든 관문»** 이다.
 * 선례는 `features/work/MyWorkPage.tsx` 의 `TaskStateCell`(`:1357-1444`)이고, 아래가 그 전수다
 * (SPEC-005 §2.6 ①②③ · WORK-005 FE-5 작업 7).
 *
 * **① 서는가 — 셋. 하나라도 어긋나면 읽기 배지다.**
 * - **가** 세션에 **`task.self_manage`** 가 있나 — **세션 봉투**의 값이고 업무 상세의 값이 아니다.
 * - **나** 업무 상세의 **접근 값이 `owner`** 인가 — **서버가 답한다.** 화면이 담당을 다시 추론하지 않는다.
 *   **수락 전 배정**은 접근 값이 이미 `read_only` 라 여기서 걸린다.
 *   ⚠ **리드라고 더 오지 않는다** — 리드 응답과 참여자 응답이 바이트 단위로 같다. 「리드면 쓰기도
 *   되겠지」로 열면 **404** 를 받는다.
 * - **다** 갈 곳이 하나라도 있나 — `allowedTaskTransitions` 가 답한다. `cancelled` 이거나
 *   **`done` 인데 승인을 기다리는** 업무는 빈 배열이라 **글자로 선다**.
 *
 * **② 골랐을 때 — 넷.** **라** 지금 상태를 다시 고르면 무동작 · **마** 허용 전이에 없으면 무동작 ·
 * **바** 「막힘」은 **`BlockReasonPrompt`** 로 간다(**사유 없이 전이가 나가지 않는다**) ·
 * **사** **요청 업무의 「완료」**는 **완료 보고 모달**로 간다(서버가 늘 거절하는 전이라, 우회가 아니라
 * **올바른 경로**다). 그 모달 안의 관문 둘(결과 요약 · 끝나지 않은 하위)은 **모달 자신의 것**이다.
 *
 * **③ 보낼 때 — 셋.** **아** 보내는 중에는 트리거가 잠긴다 · **자** **회차는 업무 상세의 것**이다
 * (`tasks[]` 행에는 `version` 이 아예 없고, 한 걸음 뒤진 회차는 **409**) ·
 * **차** 낙관적 갱신 0건 — 부르는 쪽이 **다시 읽는다**.
 *
 * **부품을 고치지 않는다** — 셋 다 `WorkModals.tsx` 가 export 한 것을 그대로 부른다.
 */
function TaskStateValue({
  row,
  detail,
  canManageOwnTasks,
  busy,
  onTransition,
  onChanged,
  onError,
  onNotice,
}: {
  row: ProjectTaskRow;
  detail: DirectTask | null;
  canManageOwnTasks: boolean;
  busy: boolean;
  onTransition?: (task: DirectTask, action: TaskAction, reason?: string) => Promise<boolean | void>;
  /** 전이가 아닌 길(완료 보고)로 상태가 바뀐 뒤 **다시 읽는** 자리. */
  onChanged?: () => Promise<void> | void;
  onError?: (message: string | null) => void;
  onNotice?: (message: string) => void;
}) {
  const [blocking, setBlocking] = useState(false);
  /** 상태 칸에서 「완료」를 고른 **요청 업무** — 전이가 아니라 보고다. */
  const [reporting, setReporting] = useState(false);

  const badge = (
    <Badge tone={taskStateTone[row.state] ?? "neutral"}>{taskStateLabel[row.state] ?? row.state}</Badge>
  );
  /* 상세가 아직 안 왔으면 회차도 접근 값도 모른다 — 모르는 동안은 배지다. */
  const owned = detail !== null && detail.access === "owner";
  const transitions = detail === null ? [] : allowedTaskTransitions(detail);
  if (!onTransition || !canManageOwnTasks || !owned || transitions.length === 0) return badge;

  /* 트리거의 톤은 상태의 톤 그대로다 — 업무 화면의 상태 셀과 같은 규칙이고, `accent` 만 기본 꼴이다. */
  const tone = taskStateTone[detail.state] ?? "neutral";
  const triggerTone = tone === "accent" ? "" : ` scax-select__trigger--${tone}`;

  return (
    <>
      <Select
        emptyActionLabel={emptyActionLabel.filter}
        label={projectScreen.metaStateSelect(row.title)}
        labels={selectLabel}
        onChange={(next) => {
          if (next === detail.state) return; // 라
          const picked = transitions.find((transition) => transition.to === next);
          if (!picked) return; // 마
          if (picked.action === "block") {
            setBlocking(true); // 바 — 사유를 받기 전에는 아무것도 나가지 않는다
            return;
          }
          if (picked.action === "complete" && isRequestTask(detail)) {
            setReporting(true); // 사 — 끝나는 길은 완료 보고 하나다
            return;
          }
          void onTransition(detail, picked.action);
        }}
        /* 지금 상태도 목록에 둔다 — 무엇이 골라져 있는지 보이고, 다시 골라도 아무 일이 없다 (라). */
        options={[
          { value: detail.state, label: taskStateLabel[detail.state] },
          ...transitions.map((transition) => ({ value: transition.to, label: taskStateLabel[transition.to] })),
        ]}
        trigger={({ label, props }) => (
          <button {...props} className={`scax-select__trigger${triggerTone}`} disabled={busy}>
            {label}
            <Icon name="chevron-down" size={12} />
          </button>
        )}
        value={detail.state}
      />
      {reporting && (
        /* `materials`·`subtasks` 를 **안 넘긴다** — 업무 화면의 상태 셀이 그렇게 부르고(선례 그대로),
           이 화면은 그 업무의 자료를 들고 있지도 않다. 모달이 자기 것을 자기가 읽는다. */
        <CompletionReportModal
          busy={busy}
          onClose={() => setReporting(false)}
          /* ⚠ **빈 함수로 막지 않는다** — 이 모달은 자기 안에 오류를 그리지 않고 이 통로로만 말한다.
             완료 거절은 갈래가 넷이다(끝나지 않은 선행 · 끝나지 않은 하위 · 요청 업무는 보고 필요 ·
             회차 불일치) — 막아 두면 사람은 버튼을 눌렀는데 «아무 일도 없는» 화면을 본다.
             선례 `MyWorkPage.tsx:1421-1422` 가 둘을 그대로 넘긴다 (D-32). */
          onError={onError ?? (() => undefined)}
          onNotice={onNotice}
          onSubmitted={async () => {
            setReporting(false);
            /* 보고가 올라갔다 — **전이를 또 보내지 않는다.** 보고 자체가 상태를 옮겼으므로
               할 일은 **다시 읽는 것** 하나다 (선례의 `onChanged` 와 같은 자리). */
            await onChanged?.();
          }}
          requesterName={detail.origin?.actor ? personName(detail.origin.actor.display_name) : "요청자"}
          task={detail}
        />
      )}
      {blocking && (
        <BlockReasonPrompt
          busy={busy}
          onClose={() => setBlocking(false)}
          onSubmit={(reason) => {
            void onTransition(detail, "block", reason);
            setBlocking(false);
          }}
          task={detail}
        />
      )}
    </>
  );
}

function RelationBlock({
  label,
  icon,
  rows,
  onTask,
}: {
  label: string;
  icon: "arrow-up" | "arrow-down";
  rows: RelationRow[];
  onTask: (taskId: string) => void;
}) {
  return (
    <div className="scax-pj-rel" data-relation={icon === "arrow-up" ? "preceding" : "succeeding"}>
      <span className="scax-pj-rel__head">
        <Icon name={icon} size={14} />
        {label}
        <span className="scax-pj-side__count">{rows.length}</span>
      </span>
      {rows.length === 0 ? (
        <p className="scax-pj-side__none">{projectScreen.none}</p>
      ) : (
        <ul className="scax-pj-rel__list">
          {rows.map((row) => (
            <li key={row.taskId}>
              {/* 읽을 수 없는 업무는 **자리가 남고 건수에 든다** — 제목만 비고 누를 수도 없다. */}
              {row.title === null ? (
                <span className="scax-pj-rel__item scax-pj-rel__item--hidden">
                  <span className="scax-pj-rel__dot" />
                  <span className="scax-pj-rel__name">{projectScreen.hiddenTask}</span>
                </span>
              ) : (
                <button className="scax-pj-rel__item" onClick={() => onTask(row.taskId)} type="button">
                  <span className="scax-pj-rel__dot" data-tone={row.state ? taskStateTone[row.state] : undefined} />
                  <span className="scax-pj-rel__name">{row.title}</span>
                  <Icon name="chevron-right" size={12} />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
