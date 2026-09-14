import { ProgressBar } from "ax-workspace-frontend";

export default { title: "General/ProgressBar", component: ProgressBar };

/**
 * 바퀴 11: `ariaLabel` 이 **필수**다 — 머리줄(`label`)이 없어도 막대는 읽혀야 한다.
 */

const box: React.CSSProperties = { width: 360 };

/** 머리줄 포함 — 이름 + done / total */
export const WithLabel = () => (
  <div style={box}><ProgressBar ariaLabel="체크리스트 진행률" label="체크리스트" done={3} total={5} /></div>
);

/** 머리줄 없음 — 세는 자리가 이미 있을 때. 그래도 이름은 남는다 */
export const Bare = () => (
  <div style={box}><ProgressBar ariaLabel="업무 진행률" done={6} total={10} /></div>
);

/** 완료 */
export const Complete = () => (
  <div style={box}><ProgressBar ariaLabel="일일보고 진행률" label="일일보고 항목" done={4} total={4} /></div>
);

/** 시작 전 */
export const Zero = () => (
  <div style={box}><ProgressBar ariaLabel="후속 업무 진행률" label="후속 업무" done={0} total={7} /></div>
);
