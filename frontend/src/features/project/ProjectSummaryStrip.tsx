import { projectScreen } from "../../lib/labels";
import type { ProjectSummary } from "./projectModel";

/**
 * 본문 ① 요약 스트립 5칸 (WORK-005 FE-1).
 *
 * **모수는 「취소를 뺀 업무」 하나**다 — 1번 칸과 5번 분모가 **같은 수**다 (D-04).
 * 업무 10건 중 2건 취소 · 8건 완료면 「전체 업무」 8 · 「완료」 8 · 진행률 100% 다.
 *
 * 「지연」은 **서버가 기한 경과일을 낸 업무의 수**다 — 화면이 「오늘」을 다시 판정하지 않는다(I-4).
 * 다른 칸과 **겹쳐 센다**: 진행 중이면서 기한을 넘긴 업무는 2번과 3번에 모두 선다.
 *
 * **전체 진행률 = 완료 칸 ÷ 전체 업무 칸**이다 — 업무별 % 의 평균이 아니다.
 * 셀 것이 없으면 「—」다: 0% 라고 말하면 「할 일이 있는데 아무것도 안 했다」가 된다.
 */
export function ProjectSummaryStrip({ summary }: { summary: ProjectSummary }) {
  const cells = [
    { key: "total", label: projectScreen.summaryTotal, value: summary.total, tone: "ink" },
    { key: "in-progress", label: projectScreen.summaryInProgress, value: summary.inProgress, tone: "accent" },
    { key: "overdue", label: projectScreen.summaryOverdue, value: summary.overdue, tone: "danger" },
    { key: "done", label: projectScreen.summaryDone, value: summary.done, tone: "positive" },
  ];
  return (
    <section aria-label={projectScreen.summaryTitle} className="scax-pj-summary">
      {cells.map((cell) => (
        <div className="scax-pj-summary__cell" data-summary={cell.key} data-tone={cell.tone} key={cell.key}>
          <span className="scax-pj-summary__label">{cell.label}</span>
          <span className="scax-pj-summary__value">{cell.value}</span>
        </div>
      ))}
      <div className="scax-pj-summary__cell scax-pj-summary__cell--wide" data-summary="percent">
        <span className="scax-pj-summary__label">{projectScreen.summaryPercent}</span>
        <span className="scax-pj-summary__meter">
          <span className="scax-pj-summary__track">
            {summary.percent === null ? null : (
              <span className="scax-pj-summary__fill" style={{ width: `${summary.percent}%` }} />
            )}
          </span>
          <span className="scax-pj-summary__pct">{summary.percent === null ? "—" : `${summary.percent}%`}</span>
        </span>
      </div>
    </section>
  );
}
