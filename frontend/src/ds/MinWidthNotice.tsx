import { Icon } from "./icons/Icon";

/**
 * Legacy design-system export retained for import compatibility.
 * The application no longer mounts it, and current styles keep it hidden for older consumers.
 *
 * 바퀴 11: 이 부품도 말을 모른다 — 프로덕션 호출부가 0곳이라 넘겨 주는 자리는 지금 검사뿐이다.
 * 부르는 쪽은 `lib/labels` 의 `minWidthNotice` 를 그대로 펼쳐 넘기면 된다.
 */
export function MinWidthNotice({ title, description }: { title: string; description: string }) {
  return (
    <div className="min-width-notice" role="alert">
      <Icon name="circle-exclamation" size={20} />
      <b>{title}</b>
      <p>{description}</p>
    </div>
  );
}
