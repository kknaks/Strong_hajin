/**
 * 도는 원 하나 — **끝을 모르는 기다림**의 자리.
 *
 * `Skeleton` 과 갈리는 축은 「올 것의 모양을 아는가」다. 스켈레톤은 «올 내용의 자리를 미리 잡는» 것이라
 * 줄 수와 높이가 실제와 같아야 뜻이 산다(디자인 시스템 v2 `10 — STATE`). 그런데 합성이 끝난 회의록이
 * 몇 줄일지는 아무도 모른다 — 거기에 막대 일곱 개를 까는 것은 «모양을 아는 척» 이고, 실제로도
 * 화면이 「무엇을 기다리는지 말하지 않는 긴 회색 줄」이 됐다(현재 화면 25).
 * 그래서 이 자리는 **도는 원 + 한 줄 문장**이다.
 *
 * 새 DS 에 `CircularCircular`(feedback)가 있지만 **우리 아카이브에는 `.d.ts` 만 있고 구현이 없다** —
 * 기하를 볼 수 없는 것을 베낄 수는 없으므로, 값은 전부 `--scax-*` 토큰으로 두고 가장 단순한 원으로 짰다.
 * DS 원본이 들어오면 이 부품의 «안» 만 갈아 끼우면 된다 — 부르는 쪽은 그대로다.
 *
 * **말은 부르는 쪽이 준다** (바퀴 11: 부품은 말을 모른다). `label` 은 화면에 보이는 한 줄이자
 * 읽어 주는 말이다 — 도는 원에는 읽을 것이 없으므로 문장이 그 일을 한다.
 */
export function Spinner({ label, size = 20 }: { label: string; size?: number }) {
  return (
    <div aria-busy="true" className="scax-spinner-row" role="status">
      <span aria-hidden className="scax-spinner" style={{ width: size, height: size }} />
      <span className="scax-spinner-row__label">{label}</span>
    </div>
  );
}
