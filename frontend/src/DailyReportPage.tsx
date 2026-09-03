import { useEffect, useState } from "react";

import { generateDailyReportDraft, getMyWork } from "./api";
import { isDirectTask, type DirectTask } from "./viewModels";

type DailyReportPageProps = {
  personaId: string;
  onError: (message: string | null) => void;
};

export function DailyReportPage({ personaId, onError }: DailyReportPageProps) {
  const [evidence, setEvidence] = useState<DirectTask[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [generationRequested, setGenerationRequested] = useState(false);

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

  const generateDraft = async () => {
    setIsGenerating(true);
    try {
      await generateDailyReportDraft(personaId);
      setGenerationRequested(true);
      onError(null);
    } catch (error) {
      onError(error instanceof Error ? error.message : "보고 초안을 만들지 못했습니다.");
    } finally {
      setIsGenerating(false);
    }
  };

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
          <p>개인 일일보고 정의만 내부 생성 operation으로 호출합니다.</p>
          <button className="primary" disabled={isGenerating} onClick={() => void generateDraft()} type="button">
            {isGenerating ? "초안 생성 중" : "초안 만들기"}
          </button>
          {generationRequested && (
            <p className="report-notice">
              초안 생성 요청을 기록했습니다. 편집 가능한 초안과 제출 이력은 DailyReport 원장 slice에서
              저장·제출 operation으로 연결됩니다.
            </p>
          )}
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
