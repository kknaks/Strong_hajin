import { Icon } from "./Icon";
import { minWidthNotice } from "./labels";

/**
 * 지원하지 않는 폭에서 뜨는 안내 (디자인 시스템 v2 `15 — RESPONSIVE`).
 *
 * 1280 미만은 지원하지 않으므로 화면을 접는 대신 이것 하나로 대신한다. 로그인 전에도 떠야 해서
 * 세션 분기 밖(앱 바깥)에 산다.
 *
 * 보임 여부는 CSS 가 정한다 — 폭을 자바스크립트로 재면 리사이즈마다 앱 전체가 다시 그려지고,
 * 그 값은 어차피 미디어쿼리가 이미 알고 있는 것이다.
 */
export function MinWidthNotice() {
  return (
    <div className="min-width-notice" role="alert">
      <Icon name="alert" size={20} />
      <b>{minWidthNotice.title}</b>
      <p>{minWidthNotice.description}</p>
    </div>
  );
}
