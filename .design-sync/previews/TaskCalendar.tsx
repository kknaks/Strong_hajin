import { TaskCalendar } from "ax-workspace-frontend";
import { useState } from "react";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/TaskCalendar", component: TaskCalendar };


type State = "open" | "in_progress" | "blocked" | "completion_submitted" | "done" | "cancelled";
const task = (id: string, title: string, state: State, start: string, due: string) => ({
  task_id: id, title, state, version: 1, block_reason: null, start_date: start, due_date: due,
});

/** 2026년 9월 — 기간이 주를 넘는 업무는 막대가 이어진다 */
const TASKS = [
  task("t1", "제품 소개서 내용 업데이트", "in_progress", "2026-09-07", "2026-09-12"),
  task("t2", "9월 영업 전략 회의 준비", "done", "2026-09-01", "2026-09-03"),
  task("t3", "2분기 실적 보고서", "blocked", "2026-09-03", "2026-09-09"),
  task("t4", "신규 고객 사례 인터뷰", "open", "2026-09-15", "2026-09-17"),
  task("t5", "디자인 팀 검토", "open", "2026-09-10", "2026-09-10"),
  task("t6", "주간 회의록 정리", "in_progress", "2026-09-08", "2026-09-08"),
  task("t7", "협력사 계약 검토", "completion_submitted", "2026-09-21", "2026-09-30"),
  task("t8", "온보딩 자료 초안", "cancelled", "2026-09-22", "2026-09-24"),
  task("t9", "홈페이지 문구 교정", "open", "2026-09-07", "2026-09-07"),
  task("t10", "월간 지표 취합", "open", "2026-09-07", "2026-09-09"),
];

const frame: React.CSSProperties = { width: 1040 };

/** 월 뷰 — 6주 격자, 칸당 막대 3줄, 넘치면 「+N개 더」 */
export const Month = () => (
  <div style={frame}>
    <TaskCalendar tasks={TASKS} onOpen={() => {}} anchorDate="2026-09-07" mode="month" />
  </div>
);

/** 주 뷰 — 칸이 높고 막대 8줄. 주/월 세그먼트는 onModeChange 를 주면 생긴다 */
export const Week = () => {
  const [mode, setMode] = useState<"week" | "month">("week");
  return (
    <div style={frame}>
      <TaskCalendar tasks={TASKS} onOpen={() => {}} anchorDate="2026-09-07" mode={mode} onModeChange={setMode} />
    </div>
  );
};

/** 비어 있는 달 */
export const EmptyMonth = () => (
  <div style={frame}>
    <TaskCalendar tasks={[]} onOpen={() => {}} anchorDate="2026-10-05" mode="month" />
  </div>
);
