import type { ReactNode } from "react";

import { IconButton } from "./Button";
import { Icon, type IconName } from "./icons/Icon";

/**
 * 파일 한 줄씩의 목록 — 종류 글리프 · 이름 · 크기 · (안 된 이유) · 빼는 자리.
 *
 * 디자인 시스템 v2 에 이 부품이 없다. `.plain-table` 은 칸이 정해진 표라 과하고, 파일 목록은
 * 「이름이 길면 줄고 크기는 오른쪽에 붙는」 한 줄이 필요하다.
 * **못 붙은 줄은 지우지 않는다** — 이름을 흐리게 두고 사유를 옆에 남긴다 (SPEC-004 §10).
 *
 * 바퀴 3b: 껍데기가 새 DS 의 `.scax-file-list` / `.scax-file-row` / `__name` 으로 갔다.
 * DS 에 없는 셋은 우리 것을 남겼다 — `.active`(열려 있는 줄) · `.muted`(못 붙은 줄) · `.file-open`(여는 단추).
 *
 * **이번 바퀴 — 줄의 «왼쪽 글리프» 와 `__size` 를 되찾았다.** 시안(08 · 12)의 파일 줄은
 * `[글리프] 이름 ……… 크기 [×]` 한 줄이고, 핸드오프도 그 순서다
 * (`work-modal.jsx` 의 `DropZone` · `workspace.v1.jsx` 의 `SideRail`).
 * 우리는 글리프를 아예 안 그렸고 크기는 `t-meta` 인라인이라 DS 규칙(`.scax-file-row__size`)이
 * 붙지 않았다. 글리프는 «부르는 쪽이 고르고»(`icon`) 기본값만 `document` 다 — 종류를 이
 * 부품이 파일 이름으로 넘겨짚지 않는다.
 */
export type FileRow = {
  key: string;
  name: string;
  size?: string;
  /** 줄 왼쪽 글리프. 주지 않으면 `document` 다 — 부르는 쪽이 파일 종류를 안다. */
  icon?: IconName;
  /** 못 붙은 이유. 있으면 이름이 흐려지고 이 문장이 옆에 선다. */
  reason?: string | null;
  /** 파일을 여는 자리. 주면 이름이 눌러지는 줄이 된다 — 드로어를 여는 것은 부르는 쪽이 정한다. */
  onOpen?: () => void;
  onRemove?: () => void;
  /** 빼기 단추의 이름 — 부르는 쪽이 준다 (바퀴 11) */
  removeLabel: string;
  /** 지금 열려 있는 줄. */
  active?: boolean;
};

export function FileList({ label, rows }: { label: string; rows: FileRow[] }) {
  return (
    <ul aria-label={label} className="scax-file-list">
      {rows.map((row) => {
        const body: ReactNode = (
          <>
            <Icon name={row.icon ?? "document"} size={20} />
            <span className={row.reason ? "scax-file-row__name muted" : "scax-file-row__name"}>{row.name}</span>
            {row.size && <span className="scax-file-row__size tabular">{row.size}</span>}
            {row.reason && (
              <span className="danger-text" style={{ flex: "none", fontSize: 12 }}>
                {row.reason}
              </span>
            )}
          </>
        );
        return (
          <li className={row.active ? "scax-file-row active" : "scax-file-row"} key={row.key}>
            {row.onOpen ? (
              <button className="file-open" onClick={row.onOpen} type="button">
                {body}
              </button>
            ) : (
              body
            )}
            {row.onRemove && (
              <IconButton name="close" size={14} label={row.removeLabel} onClick={row.onRemove} style={{ flex: "none", width: 30, height: 30, color: "var(--scax-color-ink-assistive)" }} />
            )}
          </li>
        );
      })}
    </ul>
  );
}
