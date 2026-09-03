import { useEffect, useState } from "react";

import { getActionInbox, getActions, getDailyReportStatus, getMyWork } from "./api";
import {
  isDirectTask,
  type ActionItem,
  type DailyReportStatus,
  type DirectTask,
  type ProductSurface,
  type WorkRequest,
} from "./viewModels";

type TodayPageProps = {
  personaId: string;
  canDecideWorkRequests: boolean;
  canGenerateDailyReport: boolean;
  onError: (message: string | null) => void;
  onNavigate: (surface: ProductSurface) => void;
};

export function TodayPage({
  personaId,
  canDecideWorkRequests,
  canGenerateDailyReport,
  onError,
  onNavigate,
}: TodayPageProps) {
  const [tasks, setTasks] = useState<DirectTask[]>([]);
  const [requests, setRequests] = useState<WorkRequest[]>([]);
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [reportStatus, setReportStatus] = useState<DailyReportStatus | null>(null);

  useEffect(() => {
    let cancelled = false;

    const reportDate = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Seoul" });
    const requests = canDecideWorkRequests ? getActionInbox(personaId) : Promise.resolve([]);
    void Promise.all([getMyWork(personaId), requests, getActions(personaId)])
      .then(async ([work, inbox, pendingActions]) => {
        if (cancelled) return;
        setTasks(
          work
            .filter(isDirectTask)
            .filter((task) => ["open", "in_progress", "blocked"].includes(task.state)),
        );
        setRequests(inbox);
        setActions(pendingActions.filter((action) => action.state === "pending"));
        if (!canGenerateDailyReport) {
          setReportStatus(null);
          onError(null);
          return;
        }
        try {
          setReportStatus(await getDailyReportStatus(personaId, reportDate));
        } catch (error) {
          if (!cancelled) {
            throw error;
          }
        }
        onError(null);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          onError(error instanceof Error ? error.message : "업무를 불러오지 못했습니다.");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [canDecideWorkRequests, canGenerateDailyReport, onError, personaId]);

  return (
    <>
      <section className="welcome">
        <h1>오늘의 업무를 확인하세요</h1>
        <p>확인이 필요한 요청과 이어서 진행할 업무를 한곳에서 봅니다.</p>
      </section>

      <div className="dashboard-columns">
        <section className="surface-card">
          <div className="card-title">
            <h2>확인이 필요한 업무</h2>
            <button onClick={() => onNavigate("inbox")} type="button">
              판단 보기
            </button>
          </div>
          {requests.length === 0 && actions.length === 0 ? (
            <p className="empty-row">현재 확인할 업무가 없습니다.</p>
          ) : (
            <ul className="evidence-list">
              {requests.map((request) => (
                <li key={request.request_id}>
                  <b>{request.title}</b>
                  <span>업무 요청 · {request.state}</span>
                </li>
              ))}
              {actions.map((action) => (
                <li key={action.action_id}>
                  <b>{action.title}</b>
                  <span>AX 확인 필요 · {action.payload_summary}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="surface-card">
          <div className="card-title">
            <h2>오늘 이어서 진행하는 업무</h2>
            <button onClick={() => onNavigate("work")} type="button">
              내 업무
            </button>
          </div>
          {tasks.length === 0 ? (
            <p className="empty-row">이어서 진행할 직접 생성 업무가 없습니다.</p>
          ) : (
            tasks.map((task) => (
              <article className="progress-row" key={task.task_id}>
                <div>
                  <b>{task.title}</b>
                  {task.block_reason && <small>막힘 사유: {task.block_reason}</small>}
                </div>
                <span className="status completed">{task.state}</span>
              </article>
            ))
          )}
        </section>
      </div>

      {canGenerateDailyReport && (
        <section className="surface-card schedule">
          <div className="card-title">
            <h2>보고 리마인드</h2>
            <button onClick={() => onNavigate("report")} type="button">
              보고 열기
            </button>
          </div>
          <p>{reportReminder(reportStatus)}</p>
          <button className="primary" onClick={() => onNavigate("report")} type="button">
            일일보고 작성
          </button>
        </section>
      )}
    </>
  );
}

function reportReminder(status: DailyReportStatus | null): string {
  if (status?.status === "submitted") return "오늘 보고는 제출되었습니다. 제출 이력을 확인할 수 있습니다.";
  if (status?.status === "draft") return "초안을 편집하거나 제출할 수 있습니다.";
  return "오늘의 업무 기록을 확인한 뒤 일일보고 초안을 만들 수 있습니다.";
}
