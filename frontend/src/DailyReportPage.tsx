import { useEffect, useState } from "react";

import { getMyWork } from "./api";
import { isDirectTask, type DirectTask } from "./viewModels";

type DailyReportPageProps = {
  personaId: string;
  onError: (message: string | null) => void;
};

export function DailyReportPage({ personaId, onError }: DailyReportPageProps) {
  const [evidence, setEvidence] = useState<DirectTask[]>([]);

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

  return (
    <section className="page-surface">
      <div className="card-title">
        <div>
          <p className="kicker">REPORTS · TODAY</p>
          <h2>개인 일일보고</h2>
          <p>업무 기록을 근거로 초안을 만들고, 사람이 확인한 결과만 제출합니다.</p>
        </div>
        <time dateTime="2026-09-03">2026년 9월 3일</time>
      </div>

      <div className="report-grid">
        <section className="surface-card">
          <h3>오늘의 근거</h3>
          {evidence.length === 0 ? (
            <p className="empty-row">수집된 직접 생성 업무가 없습니다.</p>
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
        </section>

        <section className="surface-card">
          <h3>초안</h3>
          <p>
            `daily_report.generate_draft` operation은 Reports application이 소유합니다. 내부의 초안 생성
            pipeline만 versioned Workflow를 사용하며, 이 화면은 generic run을 직접 호출하지 않습니다.
          </p>
        </section>

        <section className="surface-card">
          <h3>확인 및 제출</h3>
          <p>초안을 편집하고 확인한 사람만 immutable 제출을 실행할 수 있습니다.</p>
        </section>

        <section className="surface-card">
          <h3>제출 이력</h3>
          <p>제출본과 시점은 DailyReport 원장에서 조회합니다.</p>
        </section>
      </div>
    </section>
  );
}
