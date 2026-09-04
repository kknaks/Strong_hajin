import { useEffect, useMemo, useState } from "react";

import {
  editDailyReport,
  generateDailyReportDraft,
  getDailyReportHistory,
  getDailyReportStatus,
  getMyWork,
  getTask,
  getTasks,
  submitDailyReport,
} from "./api";
import { formatKoreanDate, personName, seoulToday, taskStateLabel } from "./labels";
import { StatusText } from "./WorkModals";
import {
  type DailyReportDraft,
  type DailyReportHistory,
  type DirectTask,
  type TaskState,
} from "./viewModels";

type DailyReportPageProps = {
  personaId: string;
  personaName: string;
  onError: (message: string | null) => void;
};

type ReportPhase = "not_started" | "draft" | "submitted";

const phaseLabel: Record<ReportPhase, string> = {
  not_started: "미작성",
  draft: "초안 작성 중",
  submitted: "제출됨",
};

const phaseTone: Record<ReportPhase, string> = {
  not_started: "neutral",
  draft: "progress",
  submitted: "ai",
};

export function DailyReportPage({ personaId, personaName, onError }: DailyReportPageProps) {
  const [reportDate, setReportDate] = useState(seoulToday);
  const [evidence, setEvidence] = useState<DirectTask[]>([]);
  const [draft, setDraft] = useState<DailyReportDraft | null>(null);
  const [body, setBody] = useState("");
  const [history, setHistory] = useState<DailyReportHistory | null>(null);
  const [isWorking, setIsWorking] = useState<"generate" | "edit" | "submit" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const submissions = history?.submissions ?? [];
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

  useEffect(() => {
    let cancelled = false;

    async function restoreForSelectedDate() {
      setDraft(null);
      setBody("");
      setHistory(null);
      setNotice(null);
      onError(null);
      try {
        const status = await getDailyReportStatus(reportDate);
        if (cancelled) return;
        if (!status.report_id) {
          onError(null);
          return;
        }
        const nextHistory = await getDailyReportHistory(status.report_id);
        if (cancelled) return;
        setHistory(nextHistory);
        const latestDraft = nextHistory.drafts.at(-1);
        if (!latestDraft) return;
        setDraft({
          report_id: nextHistory.report_id,
          draft_id: latestDraft.draft_id,
          draft_version: latestDraft.version,
          body: latestDraft.body,
          source_refs: latestDraft.source_refs,
          status: nextHistory.status,
        });
        setBody(latestDraft.body);
        onError(null);
      } catch (error) {
        if (!cancelled) {
          onError(error instanceof Error ? error.message : "기존 보고 초안을 불러오지 못했습니다.");
        }
      }
    }

    void restoreForSelectedDate();
    return () => {
      cancelled = true;
    };
  }, [onError, personaId, reportDate]);

  async function generateDraft() {
    if (draft && isDirty && !window.confirm("저장하지 않은 편집 내용이 있습니다. 새 초안을 만들면 편집 중인 내용은 사라집니다. 계속할까요?")) {
      return;
    }
    setIsWorking("generate");
    onError(null);
    setNotice(null);
    try {
      const nextDraft = await generateDailyReportDraft(reportDate);
      setDraft(nextDraft);
      setBody(nextDraft.body);
      setHistory(await getDailyReportHistory(nextDraft.report_id));
      setNotice(`초안 v${nextDraft.draft_version}을 만들었습니다. 내용을 확인하고 필요하면 편집한 뒤 제출하세요.`);
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
      setDraft({ ...draft, ...nextDraft });
      setBody(nextDraft.body);
      setHistory(await getDailyReportHistory(draft.report_id));
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
      setHistory(await getDailyReportHistory(draft.report_id));
      setNotice("보고를 제출했습니다. 제출본은 변경되지 않으며 정정이 필요하면 새 초안을 만들어 다시 제출합니다.");
    } catch (error) {
      onError(error instanceof Error ? error.message : "보고를 제출하지 못했습니다.");
    } finally {
      setIsWorking(null);
    }
  }

  return (
    <section className="page-surface">
      <div className="page-head">
        <div>
          <h1>개인 일일보고</h1>
          <p>오늘의 업무 활동을 근거로 초안을 만들고, 사람이 편집·확인한 내용만 제출합니다.</p>
        </div>
        <div className="page-head-actions">
          <label>
            보고일
            <input type="date" value={reportDate} onChange={(event) => setReportDate(event.target.value)} />
          </label>
        </div>
      </div>

      <div className="report-status">
        <span>
          {personName(personaName)} · {formatKoreanDate(reportDate)}
        </span>
        <span className={`badge ${phaseTone[phase]}`}>{phaseLabel[phase]}</span>
        {draft && <span className="badge outline">초안 v{draft.draft_version}</span>}
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
            <button
              className={draft ? "btn" : "btn primary"}
              disabled={isWorking !== null || !reportDate}
              onClick={() => void generateDraft()}
              type="button"
            >
              {isWorking === "generate" ? "초안 생성 중…" : draft ? "초안 다시 만들기" : "근거로 초안 만들기"}
            </button>
          </div>
          {isWorking === "generate" && (
            <p className="report-notice" style={{ marginTop: 10 }}>
              업무 기록을 모아 AI가 초안을 작성하고 있습니다. 잠시 기다려 주세요.
            </p>
          )}
        </section>

        <section className="surface-card">
          <div className="card-title">
            <h2>초안 편집</h2>
            {draft && <span className="badge outline">{isDirty ? "저장되지 않은 편집" : "저장됨"}</span>}
          </div>
          {draft ? (
            <>
              <textarea
                aria-label="일일보고 초안"
                value={body}
                onChange={(event) => setBody(event.target.value)}
                rows={10}
              />
              <div className="report-actions">
                <button
                  className="btn"
                  disabled={isWorking !== null || !body.trim() || !isDirty}
                  onClick={() => void saveEdit()}
                  type="button"
                >
                  {isWorking === "edit" ? "저장 중…" : "편집 저장"}
                </button>
                <button
                  className="btn primary"
                  disabled={isWorking !== null || isDirty}
                  onClick={() => void submit()}
                  type="button"
                >
                  {isWorking === "submit" ? "제출 중…" : "보고 제출"}
                </button>
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
          <span className="badge neutral">{submissions.length}건</span>
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
                <span>{new Date(submission.submitted_at).toLocaleString("ko-KR")}</span>
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

