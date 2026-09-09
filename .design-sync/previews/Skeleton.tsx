import { Skeleton } from "ax-workspace-frontend";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/Skeleton", component: Skeleton };


/* 실제 CSS 는 0.4초 뒤에 막대를 보인다(짧은 로딩에는 안 띄우는 규칙).
   정적 캡처는 그 전에 찍히므로 프리뷰 안에서만 지연을 0 으로 둔다 — 앱 동작은 그대로다. */
const NoDelay = () => <style>{`.skeleton-bar{animation-delay:0s}`}</style>;

/** 리스트 로딩 — 5행 (v2 10) */
export const List = () => (
  <div className="surface-card" style={{ width: 480 }}>
    <NoDelay />
    <Skeleton />
  </div>
);

/** 짧은 블록 — 실제 콘텐츠와 같은 개수로 */
export const ThreeRows = () => (
  <div style={{ width: 320 }}>
    <NoDelay />
    <Skeleton rows={3} label="회의록 불러오는 중" />
  </div>
);
