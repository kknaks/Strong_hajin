/**
 * 로딩은 빈 화면이 아니다 — 올 내용의 자리를 미리 잡아 둔다.
 *
 * 디자인 시스템 v2 `10 — STATE`: bar `--border-subtle` · r4 · height 14 · gap 10, 그리고
 * "스켈레톤은 실제 콘텐츠와 같은 개수·같은 높이로 깝니다. 리스트는 5행."
 *
 * "0.4초 안에 끝나는 로딩에는 아무것도 띄우지 않습니다" 는 CSS 가 맡는다(`.skeleton-bar` 의 지연).
 * 타이머를 컴포넌트에 두면 소비처마다 테스트가 시간에 묶이고, 짧은 로딩에서 스켈레톤이
 * 깜빡이는 것을 막는 일은 화면에 나타나는 시점의 문제이지 상태의 문제가 아니다.
 */
export function Skeleton({ rows = 5, label = "불러오는 중" }: { rows?: number; label?: string }) {
  return (
    <div aria-busy="true" className="skeleton" role="status">
      {/* 화면에는 막대만, 스크린리더에는 무엇을 기다리는지 — 막대는 읽을 것이 없다. */}
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, index) => (
        <span aria-hidden className="skeleton-bar" key={index} />
      ))}
    </div>
  );
}
