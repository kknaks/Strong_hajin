import type { ReactNode } from "react";

import { Icon } from "./Icon";

/**
 * 파일 한 줄씩의 목록 — 이름 · 크기 · (안 된 이유) · 빼는 자리.
 *
 * 디자인 시스템 v2 에 이 부품이 없다. `.plain-table` 은 칸이 정해진 표라 과하고, 파일 목록은
 * 「이름이 길면 줄고 크기는 오른쪽에 붙는」 한 줄이 필요하다.
 * **못 붙은 줄은 지우지 않는다** — 이름을 흐리게 두고 사유를 옆에 남긴다 (SPEC-004 §10).
 */
export type FileRow = {
  key: string;
  name: string;
  size?: string;
  /** 못 붙은 이유. 있으면 이름이 흐려지고 이 문장이 옆에 선다. */
  reason?: string | null;
  /** 파일을 여는 자리. 주면 이름이 눌러지는 줄이 된다 — 드로어를 여는 것은 부르는 쪽이 정한다. */
  onOpen?: () => void;
  onRemove?: () => void;
  removeLabel?: string;
  /** 지금 열려 있는 줄. */
  active?: boolean;
};

export function FileList({ label, rows }: { label: string; rows: FileRow[] }) {
  return (
    <ul aria-label={label} className="file-list">
      {rows.map((row) => {
        const body: ReactNode = (
          <>
            <span className={row.reason ? "file-name muted" : "file-name"}>{row.name}</span>
            {row.size && (
              <span className="t-meta tabular" style={{ flex: "none", fontSize: 12 }}>
                {row.size}
              </span>
            )}
            {row.reason && (
              <span className="danger-text" style={{ flex: "none", fontSize: 12 }}>
                {row.reason}
              </span>
            )}
          </>
        );
        return (
          <li className={row.active ? "file-row active" : "file-row"} key={row.key}>
            {row.onOpen ? (
              <button className="file-open" onClick={row.onOpen} type="button">
                {body}
              </button>
            ) : (
              body
            )}
            {row.onRemove && (
              <button
                aria-label={row.removeLabel ?? "빼기"}
                className="btn ghost icon h30"
                onClick={row.onRemove}
                style={{ flex: "none", width: 30, height: 30, color: "var(--text-tertiary)" }}
                type="button"
              >
                <Icon name="close" size={14} />
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
