import { ProgressBar } from "ax-workspace-frontend";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/ProgressBar", component: ProgressBar };


const box: React.CSSProperties = { width: 360 };

/** 머리줄 포함 — 이름 + done / total */
export const WithLabel = () => (
  <div style={box}><ProgressBar label="체크리스트" done={3} total={5} /></div>
);

/** 머리줄 없음 — 세는 자리가 이미 있을 때 */
export const Bare = () => (
  <div style={box}><ProgressBar done={6} total={10} /></div>
);

/** 완료 */
export const Complete = () => (
  <div style={box}><ProgressBar label="일일보고 항목" done={4} total={4} /></div>
);

/** 시작 전 */
export const Zero = () => (
  <div style={box}><ProgressBar label="후속 업무" done={0} total={7} /></div>
);
