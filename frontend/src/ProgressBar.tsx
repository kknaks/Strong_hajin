/**
 * 진행률 (디자인 시스템 v2 `11 — COMPONENTS`): 트랙 h6 `--border-subtle` r3, 채움 `--accent`.
 * 머리줄(이름 + `done / total`)은 세는 자리가 따로 없을 때만 붙인다.
 */
export function ProgressBar({ label, done, total }: { label?: string; done: number; total: number }) {
  const percent = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div className="progress">
      {/* 세는 자리가 이미 있으면 머리줄을 또 두지 않는다 — 같은 숫자를 두 번 읽히지 않기 위해서다 */}
      {label && (
        <div className="progress-head">
          <b>{label}</b>
          <span className="t-meta">
            {done} / {total}
          </span>
        </div>
      )}
      <div
        aria-label={label ?? "진행률"}
        aria-valuemax={total}
        aria-valuemin={0}
        aria-valuenow={done}
        className="progress-track"
        role="progressbar"
      >
        <span className="progress-fill" style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}
