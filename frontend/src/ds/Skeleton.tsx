/**
 * 로딩은 빈 화면이 아니다 — 올 내용의 자리를 미리 잡아 둔다.
 *
 * 바퀴 3a 에서 새 DS 로 갈았다 — 핸드오프 `scax-ui.jsx` 의 `Skeleton({variant,width})` 과
 * `components.css` 의 `.scax-skeleton*` · `.scax-skeleton-stack` 을 따른다.
 * **props 는 우리 것을 그대로 지켰다**(D-4) — 소비처 16곳이 `rows`·`label` 로 부른다.
 * 새 DS 의 `variant`·`width` 는 막대 «하나» 를 그리는 축이고 우리 `rows` 는 목록 «한 벌» 을 까는 축이라,
 * 우리 부품이 그 위에 서서 `--text` 막대를 `rows` 개 깐다. 필요해지면 variant 를 prop 으로 열면 된다.
 *
 * 「스켈레톤은 실제 콘텐츠와 같은 개수·같은 높이로 깝니다. 리스트는 5행.」 (디자인 시스템 v2 `10 — STATE`)
 *
 * **바뀐 것 하나** — 구 `.skeleton-bar` 는 `animation-delay: .4s` 로 「0.4초 안에 끝나는 로딩에는
 * 아무것도 띄우지 않는다」를 CSS 로 지켰다. 새 DS 의 `.scax-skeleton` 은 곧바로 맥동하고 지연이 없다.
 * 타이머를 컴포넌트에 두면 소비처마다 테스트가 시간에 묶이므로 여기서 되살리지 않았다 — 보고 대상이다.
 */
/** `label` 은 화면에 안 보이는 이름이다 — 부르는 쪽이 넘긴다(바퀴 11: 부품은 말을 모른다). */
export function Skeleton({ rows = 5, label }: { rows?: number; label: string }) {
  return (
    <div aria-busy="true" className="scax-skeleton-stack" role="status">
      {/* 화면에는 막대만, 스크린리더에는 무엇을 기다리는지 — 막대는 읽을 것이 없다. */}
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, index) => (
        <span aria-hidden className="scax-skeleton scax-skeleton--text scax-skeleton--w-full" key={index} />
      ))}
    </div>
  );
}
