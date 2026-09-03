import { useEffect, useState } from "react";

import { getMyWork } from "./api";
import { isDirectTask, type DirectTask, type ProductSurface } from "./viewModels";

type TodayPageProps = {
  personaId: string;
  onError: (message: string | null) => void;
  onNavigate: (surface: ProductSurface) => void;
};

export function TodayPage({ personaId, onError, onNavigate }: TodayPageProps) {
  const [tasks, setTasks] = useState<DirectTask[]>([]);

  useEffect(() => {
    let cancelled = false;

    void getMyWork(personaId)
      .then((items) => {
        if (cancelled) return;
        setTasks(items.filter(isDirectTask));
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
  }, [onError, personaId]);

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
          <p className="empty-row">
            업무 요청 판단 원장을 연결하면 권한이 있는 요청만 이곳에 표시됩니다.
          </p>
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

      <section className="surface-card schedule">
        <div className="card-title">
          <h2>보고 리마인드</h2>
          <button onClick={() => onNavigate("report")} type="button">
            보고 열기
          </button>
        </div>
        <p>오늘의 업무 기록을 확인한 뒤 일일보고를 작성하세요.</p>
        <button className="primary" onClick={() => onNavigate("report")} type="button">
          일일보고 작성
        </button>
      </section>
    </>
  );
}
