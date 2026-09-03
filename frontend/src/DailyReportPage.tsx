import { useEffect, useMemo, useState } from "react";

import {
  editDailyReport,
  generateDailyReportDraft,
  getDailyReportHistory,
  getDailyReportStatus,
  getMyWork,
  submitDailyReport,
} from "./api";
import { isDirectTask, type DailyReportDraft, type DailyReportHistory, type DirectTask } from "./viewModels";

type DailyReportPageProps = {
  personaId: string;
  onError: (message: string | null) => void;
};

function localReportDate(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Seoul" });
}

export function DailyReportPage({ personaId, onError }: DailyReportPageProps) {
  const [reportDate, setReportDate] = useState(localReportDate);
  const [evidence, setEvidence] = useState<DirectTask[]>([]);
  const [draft, setDraft] = useState<DailyReportDraft | null>(null);
  const [body, setBody] = useState("");
  const [history, setHistory] = useState<DailyReportHistory | null>(null);
  const [isWorking, setIsWorking] = useState(false);

  const submitted = useMemo(() => history?.submissions.length ?? 0, [history]);

  useEffect(() => {
    let cancelled = false;

    void getMyWork(personaId)
      .then((items) => {
        if (!cancelled) setEvidence(items.filter(isDirectTask));
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
      try {
        const status = await getDailyReportStatus(personaId, reportDate);
        if (cancelled) return;
        if (!status.report_id) {
          onError(null);
          return;
        }
        const nextHistory = await getDailyReportHistory(personaId, status.report_id);
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
    setIsWorking(true);
    onError(null);
    try {
      const nextDraft = await generateDailyReportDraft(personaId, reportDate);
      setDraft(nextDraft);
      setBody(nextDraft.body);
      setHistory(await getDailyReportHistory(personaId, nextDraft.report_id));
    } catch (error) {
      onError(error instanceof Error ? error.message : "초안을 생성하지 못했습니다.");
    } finally {
      setIsWorking(false);
    }
  }

  async function saveEdit() {
    if (!draft) return;
    setIsWorking(true);
    onError(null);
    try {
      const nextDraft = await editDailyReport(
        personaId,
        draft.report_id,
        draft.draft_id,
        draft.draft_version,
        body,
      );
      setDraft({ ...draft, ...nextDraft });
      setBody(nextDraft.body);
      setHistory(await getDailyReportHistory(personaId, draft.report_id));
    } catch (error) {
      onError(error instanceof Error ? error.message : "초안을 저장하지 못했습니다.");
    } finally {
      setIsWorking(false);
    }
  }

  async function submit() {
    if (!draft) return;
    setIsWorking(true);
    onError(null);
    try {
      await submitDailyReport(personaId, draft.report_id, draft.draft_id, draft.draft_version);
      setHistory(await getDailyReportHistory(personaId, draft.report_id));
    } catch (error) {
      onError(error instanceof Error ? error.message : "보고를 제출하지 못했습니다.");
    } finally {
      setIsWorking(false);
    }
  }

  return (
    <section className="page-surface">
      <div className="card-title">
        <div>
          <p className="kicker">REPORTS · TODAY</p>
          <h2>개인 일일보고</h2>
          <p>오늘의 업무 활동을 근거로 초안을 만들고, 사람이 편집·확인한 내용만 제출합니다.</p>
        </div>
        <label>
          보고일
          <input type="date" value={reportDate} onChange={(event) => setReportDate(event.target.value)} />
        </label>
      </div>

      <div className="report-grid">
        <section className="surface-card">
          <h3>오늘의 업무 근거</h3>
          {draft ? (
            <ul className="evidence-list">
              {draft.source_refs.map((source) => (
                <li key={`${source.task_id}-${source.task_version}-${source.occurred_at}`}>
                  <b>업무 활동</b>
                  <span>{source.state}</span>
                </li>
              ))}
            </ul>
          ) : evidence.length === 0 ? (
            <p className="empty-row">아직 확인할 업무 활동이 없습니다.</p>
          ) : (
            <ul className="evidence-list">
              {evidence.map((task) => (
                <li key={task.task_id}>
                  <b>{task.title}</b>
                  <span>{task.state}</span>
                </li>
              ))}
            </ul>
          )}
          <button type="button" disabled={isWorking || !reportDate} onClick={() => void generateDraft()}>
            {isWorking ? "초안 생성 중…" : "근거로 초안 만들기"}
          </button>
        </section>

        <section className="surface-card">
          <h3>초안 편집</h3>
          {draft ? (
            <>
              <textarea
                aria-label="일일보고 초안"
                value={body}
                onChange={(event) => setBody(event.target.value)}
                rows={8}
              />
              <button type="button" disabled={isWorking || !body.trim()} onClick={() => void saveEdit()}>
                편집 저장
              </button>
            </>
          ) : (
            <p className="empty-row">초안을 만들면 여기서 내용을 편집할 수 있습니다.</p>
          )}
        </section>

        <section className="surface-card">
          <h3>확인 및 제출</h3>
          <p>제출 후에는 해당 제출본의 본문과 근거가 변경되지 않습니다.</p>
          <button type="button" disabled={isWorking || !draft} onClick={() => void submit()}>
            보고 제출
          </button>
        </section>

        <section className="surface-card">
          <h3>제출 이력</h3>
          {submitted === 0 ? (
            <p className="empty-row">아직 제출한 버전이 없습니다.</p>
          ) : (
            <ol className="evidence-list">
              {history?.submissions.map((submission) => (
                <li key={submission.submission_id}>
                  <b>제출 v{submission.version}</b>
                  <span>{new Date(submission.submitted_at).toLocaleString("ko-KR")}</span>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
    </section>
  );
}
