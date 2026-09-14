import { Icon, Button, IconButton } from "ax-workspace-frontend";

export default { title: "General/Icon", component: Icon };

/**
 * 바퀴 4·10 에서 세트가 통째로 바뀌었다 — 26종은 새 DS 24그리드, 8종은 아직 구 16그리드(`DS-gaps` G-03 잔여),
 * `persons` 하나는 면(fill) 글리프다. 구 세트의 `check-square`·`list`·`refresh`·`alert`·`empty`·`filter` 는
 * 각각 `square-check`·`list-category`·`reset`·`circle-exclamation`·`inbox`·`tune` 로 이름이 바뀌었다.
 */
const GRID24 = [
  "arrow-right", "blank", "business-bag", "calendar", "check", "chevron-down", "chevron-right",
  "circle-exclamation", "clock", "close", "collapse", "company", "document", "expand", "home",
  "inbox", "left-side", "link", "list-category", "minus", "pencil", "play", "plus", "reset",
  "search", "send", "square-check", "trash", "tune",
] as const;

/** 아직 구 16그리드인 8종 — 선 두께를 16/24 만큼 줄여 보이는 굵기를 맞춘다 */
const GRID16 = ["arrow-down", "arrow-up", "ban", "circle", "folder", "paperclip", "pending", "sparkle"] as const;

/** 시안이 「면」으로 준 글리프 — stroke 가 아니라 fill 로 그린다 */
const FILL16 = ["persons"] as const;

const cell: React.CSSProperties = { display: "grid", justifyItems: "center", gap: 6, padding: "10px 4px", color: "var(--scax-color-ink-assistive)" };
const caption: React.CSSProperties = { fontSize: 11, color: "var(--scax-color-ink-assistive)" };
const grid: React.CSSProperties = { display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 4, width: 560 };

/** 38종 전부 · 16px 기본 */
export const AllGlyphs = () => (
  <div>
    <p className="t-meta" style={{ marginBottom: 6 }}>24 그리드 · {GRID24.length}종</p>
    <div style={grid}>
      {GRID24.map((name) => (
        <div key={name} style={cell}>
          <Icon name={name} size={16} />
          <span style={caption}>{name}</span>
        </div>
      ))}
    </div>
    <p className="t-meta" style={{ margin: "16px 0 6px" }}>16 그리드 · {GRID16.length}종 (G-03 잔여)</p>
    <div style={grid}>
      {GRID16.map((name) => (
        <div key={name} style={cell}>
          <Icon name={name} size={16} />
          <span style={caption}>{name}</span>
        </div>
      ))}
    </div>
    <p className="t-meta" style={{ margin: "16px 0 6px" }}>면 글리프 · {FILL16.length}종</p>
    <div style={grid}>
      {FILL16.map((name) => (
        <div key={name} style={cell}>
          <Icon name={name} size={16} />
          <span style={caption}>{name}</span>
        </div>
      ))}
    </div>
  </div>
);

/** 렌더 크기 4종 — 12 는 셰브런 전용 */
export const Sizes = () => (
  <div style={{ display: "flex", alignItems: "flex-end", gap: 28, color: "var(--scax-color-ink-assistive)" }}>
    {([12, 14, 16, 20] as const).map((size) => (
      <div key={size} style={cell}>
        <Icon name={size === 12 ? "chevron-down" : "search"} size={size} />
        <span style={caption}>
          {size === 12 ? "12 · 셰브런" : size === 14 ? "14 · 목록·배지" : size === 16 ? "16 · 기본" : "20 · 툴바·빈 상태"}
        </span>
      </div>
    ))}
  </div>
);

/** 색은 부모의 currentColor */
export const Colors = () => (
  <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
    <span style={{ ...cell, color: "var(--scax-color-ink-assistive)" }}><Icon name="calendar" /><span style={caption}>기본</span></span>
    <span style={{ ...cell, color: "var(--scax-color-ink)" }}><Icon name="calendar" /><span style={caption}>활성</span></span>
    <span style={{ ...cell, color: "var(--scax-color-ink-inverse)", background: "var(--scax-color-accent)", borderRadius: 8 }}>
      <Icon name="calendar" />
      <span style={{ ...caption, color: "var(--scax-color-ink-inverse)" }}>채운 면 위</span>
    </span>
    <span style={{ ...cell, color: "var(--scax-color-ink-disabled)" }}><Icon name="calendar" /><span style={caption}>비활성</span></span>
  </div>
);

/** 글자와 나란히 — 단추 안의 글리프는 `iconBefore`, 아이콘만이면 `IconButton` */
export const InButtons = () => (
  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
    <Button variant="solid" tone="primary" iconBefore="plus" label="새 업무" />
    <Button iconBefore="reset" label="새로고침" />
    <Button variant="ai" iconBefore="sparkle" label="AX 에게 묻기" />
    <IconButton name="search" label="검색" size={16} />
    <IconButton name="close" label="닫기" size={14} />
  </div>
);
