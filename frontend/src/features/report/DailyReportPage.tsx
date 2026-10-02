import { recallScreenValue, rememberScreenValue, useRemembered, useScreenEpoch } from "../../lib/screenCache";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type React from "react";
import { Badge, type BadgeTone } from "../../ds/Badge";
import { Button } from "../../ds/Button";
import { DateField } from "../../ds/DateField";

import {
  editDailyReport,
  generateDailyReportDraft,
  getDailyReportHistory,
  getDailyReportStatus,
  getMyWork,
  getTask,
  getTasks,
  submitDailyReport,
} from "../../lib/api";
import { datePickerLabel, formatDate, formatDateTime, formatMonthLong, personName, seoulToday, taskStateLabel, weekdayNames } from "../../lib/labels";
import { StatusText } from "../work/WorkModals";
import {
  type DailyReportDraft,
  type DailyReportHistory,
  type DailyReportStatus,
  type DirectTask,
  type TaskState,
} from "../../lib/viewModels";

type DailyReportPageProps = {
  personaId: string;
  personaName: string;
  onError: (message: string | null) => void;
  /** Registers this surface's reload so the shell can await it after an approved AX effect (no remount). */
  onRegisterRefresh?: (refresh: (() => Promise<void>) | null) => void;
  /** 바퀴 5a J-2 가 만든 자리 — 이 화면의 머리 행동을 셸 머리줄에 올린다. 떠날 때 null 로 지운다. */
  onRegisterHeaderActions?: (node: React.ReactNode) => void;
};

type ReportPhase = "not_started" | "draft" | "submitted";

const phaseLabel: Record<ReportPhase, string> = {
  not_started: "미작성",
  draft: "초안 작성 중",
  submitted: "제출됨",
};

/**
 * 바퀴 8-A: 구 `.badge.{neutral|progress|ai}` 세 톤을 새 `Badge` 의 톤으로 옮겼다.
 *
 * `progress`(구 `--progress-*` 파랑)는 `DS-gaps` G-16 이 「tint 짝이 없다」고 적은 자리인데,
 * **배지에 한해서는 짝이 이미 있다** — `components.css:179` 의 `.scax-badge--info` 가
 * 바탕 `rgba(0,116,204,.08)` + 글자 `--scax-color-info` 로 그 쌍을 들고 있다.
 * 그래서 여기서는 발명 없이 `info` 로 간다. G-16 은 배지 «밖» 의 자리(진행 행 등)에 그대로 열려 있다.
 */
const phaseTone: Record<ReportPhase, BadgeTone> = {
  not_started: "neutral",
  draft: "info",
  submitted: "accent",
};

/** 날짜 하나의 보고 — 생성 상태 · 이력 · 최신 초안. 화면 데이터 기억의 한 칸이다. */
type ReportSnapshot = {
  status: DailyReportStatus["generation_status"];
  history: DailyReportHistory | null;
  draft: DailyReportDraft | null;
};
const reportSnapshotKey = (date: string) => `report.day:${date}`;
/**
 * 기억에서 되살리는 생성 상태 (fix1 · 검수 WARN-5). 「생성 중」(queued·running)은 되살리지 않는다 — 그새 끝났을 수
 * 있는 진행 표시가 잠깐 비치고 폴링이 옛 상태로 다시 도는 것을 막는다. 정말 진행 중이면 갱신 응답이 곧 다시 세운다.
 */
const restoredStatus = (status: ReportSnapshot["status"] | undefined): ReportSnapshot["status"] =>
  status === "queued" || status === "running" ? null : status ?? null;

export function DailyReportPage({ personaId, personaName, onError, onRegisterHeaderActions, onRegisterRefresh }: DailyReportPageProps) {
  const [reportDate, setReportDate] = useState(seoulToday);
  /*
   * 받아 둔 화면 데이터로 시작한다 (WORK-008 Phase 2 · `lib/screenCache.ts`). 탭을 옮겨 갔다 돌아오면
   * 근거 업무와 그 날의 보고(초안·이력·생성 상태)를 바로 그리고, 진입 effect 가 뒤에서 다시 읽어 갈아 끼운다.
   * 보고는 날짜마다 따로 기억한다. 쓰던 글(편집 중인 본문)은 기억하지 않는다 — 받아 둔 초안 본문으로 시작한다.
   */
  /** 이 마운트의 세대 — 날짜별 보고를 직접 기억하는 자리가 넘긴다 (fix1). */
  const screenEpoch = useScreenEpoch();
  const [initialSnapshot] = useState(() => recallScreenValue<ReportSnapshot>(reportSnapshotKey(seoulToday())));
  const [evidence, setEvidence] = useRemembered<DirectTask[]>("report.evidence", []);
  const [draft, setDraft] = useState<DailyReportDraft | null>(initialSnapshot?.draft ?? null);
  const [body, setBody] = useState(initialSnapshot?.draft?.body ?? "");
  const [history, setHistory] = useState<DailyReportHistory | null>(initialSnapshot?.history ?? null);
  const [isWorking, setIsWorking] = useState<"generate" | "edit" | "submit" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [generationStatus, setGenerationStatus] = useState<DailyReportStatus["generation_status"]>(restoredStatus(initialSnapshot?.status));
  /**
   * 이 날의 응답을 받았나 (fix1 · WARN-1). 받아 둔 초안·이력으로 그린 생성·저장·제출 단추는 갱신 응답이 올 때까지
   * 잠근다 — 기억한 회차로 명령을 보내지 않는다. 받아 둔 것 없이 시작했으면 처음부터 참이다.
   */
  const [reportFresh, setReportFresh] = useState(!initialSnapshot);

  const submissions = history?.submissions ?? [];
  const generationActive = generationStatus === "queued" || generationStatus === "running";
  const generationBlocked = generationActive || generationStatus === "needs_verification";
  const phase: ReportPhase = submissions.length > 0 ? "submitted" : draft ? "draft" : "not_started";
  const [resolvedTitles, setResolvedTitles] = useState<Record<string, string>>({});
  const taskTitles = useMemo(
    () => new Map([...Object.entries(resolvedTitles), ...evidence.map((task) => [task.task_id, task.title] as const)]),
    [evidence, resolvedTitles],
  );
  const isDirty = draft !== null && body !== draft.body;
  const groupedSources = useMemo(() => groupSourceRefs(draft?.source_refs ?? []), [draft]);

  useEffect(() => {
    const unknownIds = groupedSources
      .map((group) => group.taskId)
      .filter((taskId) => !taskTitles.has(taskId));
    if (unknownIds.length === 0) return;
    let cancelled = false;
    void Promise.all(
      unknownIds.map((taskId) => getTask(taskId).then((task) => [taskId, task.title] as const).catch(() => null)),
    ).then((entries) => {
      if (cancelled) return;
      const found = entries.filter((entry): entry is readonly [string, string] => entry !== null);
      if (found.length > 0) setResolvedTitles((current) => ({ ...current, ...Object.fromEntries(found) }));
    });
    return () => {
      cancelled = true;
    };
  }, [groupedSources, personaId, taskTitles]);
  const activeEvidence = useMemo(
    () => evidence.filter((task) => task.state !== "cancelled"),
    [evidence],
  );

  useEffect(() => {
    let cancelled = false;

    void getTasks()
      .catch(async () => getMyWork())
      .then((items) => {
        if (!cancelled) setEvidence(items);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          onError(error instanceof Error ? error.message : "보고 근거를 불러오지 못했습니다.");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [onError, personaId]);

  const isDirtyRef = useRef(false);
  isDirtyRef.current = isDirty;

  /**
   * Re-reads the report for the selected date. `reset` clears the editor first (a new date or persona starts clean);
   * a plain refresh keeps an unsaved edit so an approved effect elsewhere never discards what the user is typing.
   * Throws on failure so the caller can decide between an inline error and a retryable stale banner.
   */
  const loadReport = useCallback(
    async ({ reset = false, shouldApply = () => true }: { reset?: boolean; shouldApply?: () => boolean } = {}) => {
      if (reset) {
        setDraft(null);
        setBody("");
        setHistory(null);
        setNotice(null);
        setGenerationStatus(null);
      }
      const status = await getDailyReportStatus(reportDate);
      if (!shouldApply()) return;
      setGenerationStatus(status.generation_status);
      if (!status.report_id) {
        if (!reset) {
          setDraft(null);
          setHistory(null);
        }
        rememberScreenValue<ReportSnapshot>(reportSnapshotKey(reportDate), { status: status.generation_status, history: null, draft: null }, screenEpoch);
        setReportFresh(true);
        return status;
      }
      const nextHistory = await getDailyReportHistory(status.report_id);
      if (!shouldApply()) return;
      setHistory(nextHistory);
      const latestDraft = nextHistory.drafts.at(-1);
      if (!latestDraft) {
        rememberScreenValue<ReportSnapshot>(reportSnapshotKey(reportDate), { status: status.generation_status, history: nextHistory, draft: null }, screenEpoch);
        setReportFresh(true);
        return;
      }
      const nextDraft: DailyReportDraft = {
        report_id: nextHistory.report_id,
        draft_id: latestDraft.draft_id,
        draft_version: latestDraft.version,
        body: latestDraft.body,
        source_refs: latestDraft.source_refs,
        status: nextHistory.status,
      };
      setDraft(nextDraft);
      rememberScreenValue<ReportSnapshot>(reportSnapshotKey(reportDate), { status: status.generation_status, history: nextHistory, draft: nextDraft }, screenEpoch);
      // 어떤 다시 읽기든(진입·셸 refresh·생성 폴링) 이 날의 응답을 받으면 잠금이 풀린다 (fix2 · WARN-A).
      setReportFresh(true);
      setBody((current) => (reset || !isDirtyRef.current ? latestDraft.body : current));
      return status;
    },
    [reportDate, screenEpoch],
  );

  useEffect(() => {
    let cancelled = false;
    onError(null);
    /* 이 날의 보고를 받아 둔 적이 있으면 그것을 먼저 세우고 «비우지 않고» 다시 읽는다 — 빈 화면이 번쩍이지
       않는다. 없으면 지금처럼 비운 채 읽는다(새 날짜 · 새 사람은 깨끗이 시작한다). */
    const snapshot = recallScreenValue<ReportSnapshot>(reportSnapshotKey(reportDate));
    if (snapshot) {
      setDraft(snapshot.draft);
      setBody(snapshot.draft?.body ?? "");
      setHistory(snapshot.history);
      setNotice(null);
      setGenerationStatus(restoredStatus(snapshot.status));
    }
    setReportFresh(!snapshot);
    void loadReport({ reset: !snapshot, shouldApply: () => !cancelled })
      .catch((error: unknown) => {
      if (!cancelled) onError(error instanceof Error ? error.message : "기존 보고 초안을 불러오지 못했습니다.");
    });
    return () => {
      cancelled = true;
    };
  }, [loadReport, onError, personaId, reportDate]);

  /* 보고일은 이 화면의 유일한 머리 행동이다 — 셸 머리줄에 올린다 (바퀴 8-C). */
  useEffect(() => {
    if (!onRegisterHeaderActions) return;
    onRegisterHeaderActions(
      <DateField
          formatMonth={formatMonthLong}
          labels={datePickerLabel}
          today={seoulToday()}
          weekdayNames={weekdayNames} id="report-date" label="보고일" onChange={(next) => next && setReportDate(next)} value={reportDate} />,
    );
    return () => onRegisterHeaderActions(null);
  }, [onRegisterHeaderActions, reportDate]);

  useEffect(() => {
    if (generationStatus !== "queued" && generationStatus !== "running") return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void loadReport({ shouldApply: () => !cancelled })
        .then((status) => {
          if (cancelled || !status) return;
          if (status.generation_status === "completed") {
            setNotice("일일보고 초안이 준비되었습니다. 내용을 확인하고 필요하면 편집한 뒤 제출하세요.");
          } else if (status.generation_status === "failed") {
            onError("일일보고 초안을 만들지 못했습니다. 다시 시도해 주세요.");
          } else if (status.generation_status === "needs_verification") {
            onError("AI 실행 결과가 불확실합니다. 새 초안을 만들기 전에 운영 기록을 확인해 주세요.");
          }
        })
        .catch((error: unknown) => {
          if (!cancelled) onError(error instanceof Error ? error.message : "보고 생성 상태를 확인하지 못했습니다.");
        });
    }, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [generationStatus, loadReport, onError]);

  // The shell awaits this after an approved AX effect; unsaved editor text is preserved.
  const refreshReport = useCallback(async () => {
    await loadReport();
  }, [loadReport]);
  useEffect(() => {
    onRegisterRefresh?.(refreshReport);
    return () => onRegisterRefresh?.(null);
  }, [onRegisterRefresh, refreshReport]);

  /** 쓰기 뒤의 새 값을 그 날의 기억에도 남긴다 — 다시 들어왔을 때 옛 초안·이력이 비치지 않는다. */
  function rememberReport(patch: Partial<ReportSnapshot>) {
    const key = reportSnapshotKey(reportDate);
    const current = recallScreenValue<ReportSnapshot>(key) ?? { status: generationStatus, history, draft };
    rememberScreenValue<ReportSnapshot>(key, { ...current, ...patch }, screenEpoch);
  }

  async function generateDraft() {
    if (draft && isDirty && !window.confirm("저장하지 않은 편집 내용이 있습니다. 새 초안을 만들면 편집 중인 내용은 사라집니다. 계속할까요?")) {
      return;
    }
    setIsWorking("generate");
    onError(null);
    setNotice(null);
    try {
      const accepted = await generateDailyReportDraft(reportDate);
      setGenerationStatus(accepted.generation_status);
      rememberReport({ status: accepted.generation_status });
      setNotice("일일보고 초안 생성을 접수했습니다. 이 페이지를 닫아도 계속 진행됩니다.");
    } catch (error) {
      onError(error instanceof Error ? error.message : "초안을 생성하지 못했습니다.");
    } finally {
      setIsWorking(null);
    }
  }

  async function saveEdit() {
    if (!draft) return;
    setIsWorking("edit");
    onError(null);
    setNotice(null);
    try {
      const nextDraft = await editDailyReport(draft.report_id,
        draft.draft_id,
        draft.draft_version,
        body,
      );
      const savedDraft = { ...draft, ...nextDraft };
      setDraft(savedDraft);
      setBody(nextDraft.body);
      rememberReport({ draft: savedDraft });
      const savedHistory = await getDailyReportHistory(draft.report_id);
      setHistory(savedHistory);
      rememberReport({ history: savedHistory });
      setNotice(`편집을 초안 v${nextDraft.draft_version}으로 저장했습니다.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "초안을 저장하지 못했습니다.");
    } finally {
      setIsWorking(null);
    }
  }

  async function submit() {
    if (!draft) return;
    if (isDirty) {
      onError("편집한 내용을 먼저 저장한 뒤 제출해 주세요.");
      return;
    }
    setIsWorking("submit");
    onError(null);
    setNotice(null);
    try {
      await submitDailyReport(draft.report_id, draft.draft_id, draft.draft_version);
      const submittedHistory = await getDailyReportHistory(draft.report_id);
      setHistory(submittedHistory);
      rememberReport({ history: submittedHistory });
      setNotice("보고를 제출했습니다. 제출본은 변경되지 않으며 정정이 필요하면 새 초안을 만들어 다시 제출합니다.");
    } catch (error) {
      onError(error instanceof Error ? error.message : "보고를 제출하지 못했습니다.");
    } finally {
      setIsWorking(null);
    }
  }

  return (
    <section className="page-surface">
      {/* 바퀴 8-C: 페이지 h1 「개인 일일보고」를 걷어냈다 — 셸 머리(.scax-page-header > h1)가 「보고」를
          이미 이고 있어서 같은 화면에 제목 줄이 둘이었다(바퀴 5a J-2 · 8-B 가 세 화면에 한 것과 같다).
          설명 문단은 값이라 얇은 줄이 받고, 보고일 고르는 자리는 «행동» 이라 셸 머리줄로 올렸다. */}
      <div className="screens-b-lead">
        <p className="screens-b-lead__text">오늘의 업무 활동을 근거로 초안을 만들고, 사람이 편집·확인한 내용만 제출합니다.</p>
      </div>

      <div className="report-status">
        <span>
          {personName(personaName)} · {formatDate(reportDate)}
        </span>
        <Badge tone={phaseTone[phase]}>{phaseLabel[phase]}</Badge>
        {/* 「초안 vN」·「저장됨」은 구 `.badge.outline`(흰 바탕 + 테두리) 그대로 남긴다 — `DS-gaps` G-29.
            새 `.scax-badge--neutral` 은 회색 «채움» 이라 접으면 채움형 neutral 과 뜻이 겹친다.
            대가: 이 줄에서 구 배지(h20·r4)와 새 배지(h22·r6)가 나란히 서서 눈에 어긋난다.
            G-29 가 닫히면 두 줄 다 `Badge` 로 간다. */}
        {draft && <Badge tone="outline">초안 v{draft.draft_version}</Badge>}
      </div>

      {notice && (
        <p className="report-notice" role="status" style={{ marginBottom: 16 }}>
          {notice}
        </p>
      )}

      <div className="report-grid">
        <section className="surface-card">
          <div className="card-title">
            <h2>{draft ? "초안에 쓰인 업무 기록" : "오늘의 업무 근거"}</h2>
          </div>
          {draft ? (
            groupedSources.length === 0 ? (
              <p className="empty-row">이 초안에 연결된 업무 기록이 없습니다.</p>
            ) : (
              <ul className="evidence-list evidence-chips">
                {groupedSources.map((group) => (
                  <li key={group.taskId}>
                    <span className="evidence-states">
                      {group.states.map((state, index) => (
                        <StatusText key={`${state}-${index}`} label={labelFor(state)} state={state} />
                      ))}
                    </span>
                    <span>{taskTitles.get(group.taskId) ?? `업무 ${group.taskId.slice(0, 8)}`}</span>
                  </li>
                ))}
              </ul>
            )
          ) : activeEvidence.length === 0 ? (
            <p className="empty-row">아직 확인할 업무 활동이 없습니다.</p>
          ) : (
            <ul className="evidence-list evidence-chips">
              {activeEvidence.map((task) => (
                <li key={task.task_id}>
                  <StatusText state={task.state} />
                  <span>{task.title}</span>
                </li>
              ))}
            </ul>
          )}
          <div className="report-actions">
            {/* 바퀴 12: 껍데기는 우리 Button(바퀴 3c), 막는 조건은 main(#10)의 generationBlocked 다 */}
            <Button
              disabled={isWorking !== null || generationBlocked || !reportDate || !reportFresh}
              onClick={() => void generateDraft()}
              size="sm"
              tone={draft ? "neutral" : "primary"}
              type="button"
              variant={draft ? "outlined" : "solid"}
            >
              {generationStatus === "needs_verification"
                ? "생성 결과 확인 필요"
                : isWorking === "generate" || generationActive
                  ? "초안 생성 중…"
                  : draft
                    ? "초안 다시 만들기"
                    : "근거로 초안 만들기"}
            </Button>
          </div>
          {(isWorking === "generate" || generationActive) && (
            <p className="report-notice" style={{ marginTop: 10 }}>
              업무 기록을 모아 AI가 초안을 작성하고 있습니다. 잠시 기다려 주세요.
            </p>
          )}
          {generationStatus === "needs_verification" && (
            <p className="report-notice danger-text" role="status" style={{ marginTop: 10 }}>
              AI 실행 결과가 불확실합니다. 새 요청을 보내기 전에 운영 기록을 확인해 주세요.
            </p>
          )}
        </section>

        <section className="surface-card">
          <div className="card-title">
            <h2>초안 편집</h2>
            {draft && <Badge tone="outline">{isDirty ? "저장되지 않은 편집" : "저장됨"}</Badge>}
          </div>
          {draft ? (
            <>
              {/* 바퀴 8-A: 맨 `<textarea>` 를 새 DS 의 여러 줄 입력 골격으로 감쌌다
                  (`components.css` `.scax-composer` / `.scax-composer__input`).
                  `aria-label` 은 그대로다 — `App.test.tsx:714` 가 이 이름으로 칸을 찾는다. */}
              <div className="scax-composer">
                <textarea
                  aria-label="일일보고 초안"
                  className="scax-composer__input"
                  value={body}
                  onChange={(event) => setBody(event.target.value)}
                  rows={10}
                />
              </div>
              <div className="report-actions">
                <Button size="sm" disabled={isWorking !== null || !body.trim() || !isDirty || !reportFresh} onClick={() => void saveEdit()}
                  type="button"
                >
                  {isWorking === "edit" ? "저장 중…" : "편집 저장"}
                </Button>
                <Button variant="solid" tone="primary" size="sm" disabled={isWorking !== null || isDirty || !reportFresh} onClick={() => void submit()}
                  type="button"
                >
                  {isWorking === "submit" ? "제출 중…" : "보고 제출"}
                </Button>
              </div>
              <p className="report-notice" style={{ marginTop: 10 }}>
                제출 후에는 해당 제출본의 본문과 근거가 변경되지 않습니다.
              </p>
            </>
          ) : (
            <p className="empty-row">초안을 만들면 여기서 내용을 편집하고 제출할 수 있습니다.</p>
          )}
        </section>
      </div>

      <section className="surface-card report-history" style={{ marginTop: 20 }}>
        <div className="card-title">
          <h2>제출 이력</h2>
          <Badge >{submissions.length}건</Badge>
        </div>
        {submissions.length === 0 ? (
          <p className="empty-row">아직 제출한 버전이 없습니다.</p>
        ) : (
          <ol className="evidence-list">
            {submissions.map((submission) => (
              <li key={submission.submission_id}>
                <div>
                  <b>제출 v{submission.version}</b>
                  <p>{submission.body}</p>
                  {submission.reason && <small>정정 사유: {submission.reason}</small>}
                </div>
                <span>{formatDateTime(submission.submitted_at)}</span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </section>
  );
}

function groupSourceRefs(sources: DailyReportDraft["source_refs"]): Array<{ taskId: string; states: string[] }> {
  const ordered = [...sources].sort((left, right) => left.occurred_at.localeCompare(right.occurred_at));
  const groups = new Map<string, string[]>();
  for (const source of ordered) {
    const states = groups.get(source.task_id) ?? [];
    if (states.at(-1) !== source.state) states.push(source.state);
    groups.set(source.task_id, states);
  }
  return [...groups.entries()].map(([taskId, states]) => ({ taskId, states }));
}

function labelFor(state: string): string {
  return state in taskStateLabel ? taskStateLabel[state as TaskState] : state;
}
