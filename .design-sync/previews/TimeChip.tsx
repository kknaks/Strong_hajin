import { TimeChip } from "ax-workspace-frontend";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/TimeChip", component: TimeChip };


const row: React.CSSProperties = { display: "inline-flex", alignItems: "center", gap: 4 };

/** 누르는 칩 — 회의록 줄이 딛는 근거 구간. 누르면 스크립트의 그 자리가 열린다 */
export const Jump = () => (
  <span style={row}>
    <TimeChip label="15:31" onClick={() => {}} />
  </span>
);

/** 구간이 여럿인 줄 — 근거를 전부 낸다. 시각 오름차순 (D49) */
export const Many = () => (
  <span style={row}>
    <TimeChip label="15:31" onClick={() => {}} />
    <TimeChip label="15:32" onClick={() => {}} />
    <TimeChip label="15:40" onClick={() => {}} />
  </span>
);

/** 읽기만 하는 칩 — 갈 자리가 없을 때(회의록 편집 중·참석자가 아닌 창). 크기는 그대로다 */
export const Static = () => (
  <span style={row}>
    <TimeChip label="15:31" />
  </span>
);
