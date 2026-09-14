import { MinWidthNotice } from "ax-workspace-frontend";

export default { title: "General/MinWidthNotice", component: MinWidthNotice };

/**
 * 1280 미만에서만 보인다(CSS 미디어쿼리) — 카드 뷰포트가 1280 보다 좁아야 화면에 뜬다.
 *
 * 바퀴 11: 이 부품도 말을 모른다 — `title`·`description` 을 부르는 쪽이 준다.
 * 앱의 프로덕션 호출부는 0곳이고, `lib/labels` 의 `minWidthNotice` 를 그대로 펼쳐 넘기면 된다.
 */
export const Notice = () => (
  <div style={{ minHeight: 400 }}>
    <MinWidthNotice
      description="가로 1280 이상에서 사용해 주세요. 창을 넓히면 바로 이어서 볼 수 있습니다."
      title="화면이 좁습니다"
    />
  </div>
);
