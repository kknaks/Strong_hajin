/**
 * 시각 칩 (CMP-TIME-CHIP) — 「그 자리로 간다」를 누르는 자리로 낸 시각 하나.
 *
 * 회의록 줄이 딛는 근거 구간이 이것으로 서고(SCR-106-I05), 누르면 스크립트의 그 구간이 열린다.
 * 맨글자 시각과 달리 테두리와 라운드를 두어 «누를 수 있는 것» 으로 보이게 한다 (D49).
 * `onClick` 이 없으면 같은 모양의 읽는 칩이다 — 크기가 바뀌지 않아 줄이 흔들리지 않는다.
 */
export function TimeChip({ label, onClick, title }: { label: string; onClick?: () => void; title?: string }) {
  if (!onClick) {
    return (
      <span className="time-chip" title={title}>
        {label}
      </span>
    );
  }
  return (
    <button className="time-chip" onClick={onClick} title={title} type="button">
      {label}
    </button>
  );
}
