import { Skeleton } from "ax-workspace-frontend";

export default { title: "General/Skeleton", component: Skeleton };

/**
 * 바퀴 9-B 에서 구 `.skeleton-bar` 가 새 DS 의 `.scax-skeleton` 으로 갔다.
 * **구 CSS 의 `animation-delay: .4s` 가 사라졌다** — 지금은 곧바로 맥동하므로,
 * 예전 프리뷰가 갖고 있던 `NoDelay` 우회는 필요 없고 지웠다.
 *
 * 바퀴 11: `label` 이 **필수**다 — 막대는 읽을 것이 없으므로 무엇을 기다리는지는 부르는 쪽이 준다.
 */

/** 리스트 로딩 — 실제 행 수와 같게, 리스트는 5행 */
export const List = () => (
  <div className="surface-card" style={{ width: 480 }}>
    <Skeleton label="업무 목록 불러오는 중" />
  </div>
);

/** 짧은 블록 — 실제 콘텐츠와 같은 개수로 */
export const ThreeRows = () => (
  <div style={{ width: 320 }}>
    <Skeleton label="회의록 불러오는 중" rows={3} />
  </div>
);

/** 한 줄짜리 자리 */
export const Single = () => (
  <div style={{ width: 260 }}>
    <Skeleton label="담당자 불러오는 중" rows={1} />
  </div>
);
