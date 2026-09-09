import { MinWidthNotice } from "ax-workspace-frontend";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/MinWidthNotice", component: MinWidthNotice };


/** 1280 미만은 지원하지 않는 폭 — 화면을 접는 대신 안내 하나 (v2 15). 카드 뷰포트가 1280 보다 좁아야 보인다 */
export const Notice = () => (
  <div style={{ minHeight: 400 }}>
    <MinWidthNotice />
  </div>
);
