import { Icon } from "ax-workspace-frontend";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/Icon", component: Icon };


const NAMES = [
  "close", "chevron-down", "chevron-right", "arrow-right", "arrow-up", "arrow-down", "sparkle", "send", "check",
  "check-square", "square", "circle", "play", "list", "calendar", "paperclip", "plus", "minus", "home", "refresh",
  "ban", "pending", "alert", "search", "empty", "filter",
] as const;

const cell: React.CSSProperties = { display: "grid", justifyItems: "center", gap: 6, padding: "10px 4px", color: "var(--text-tertiary)" };
const caption: React.CSSProperties = { fontSize: 11, color: "var(--text-tertiary)" };

/** 26 종 전부 · 16px 기본 · Text Tertiary */
export const AllGlyphs = () => (
  <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 4, width: 560 }}>
    {NAMES.map((name) => (
      <div key={name} style={cell}>
        <Icon name={name} size={16} />
        <span style={caption}>{name}</span>
      </div>
    ))}
  </div>
);

/** 렌더 크기 3종 + chevron 12 (v2 07) */
export const Sizes = () => (
  <div style={{ display: "flex", alignItems: "flex-end", gap: 28, color: "var(--text-tertiary)" }}>
    {([12, 14, 16, 20] as const).map((size) => (
      <div key={size} style={cell}>
        <Icon name={size === 12 ? "chevron-down" : "search"} size={size} />
        <span style={caption}>{size === 12 ? "12 · chevron" : size === 14 ? "14 · 리스트·배지" : size === 16 ? "16 · 기본" : "20 · 툴바·빈 상태"}</span>
      </div>
    ))}
  </div>
);

/** 색은 부모의 currentColor — 기본 · 활성(현재 위치) · Primary 위 · 비활성 */
export const Colors = () => (
  <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
    <span style={{ ...cell, color: "var(--text-tertiary)" }}><Icon name="calendar" /><span style={caption}>기본</span></span>
    <span style={{ ...cell, color: "var(--text-primary)" }}><Icon name="calendar" /><span style={caption}>활성</span></span>
    <span style={{ ...cell, color: "var(--on-fill)", background: "var(--action)", borderRadius: 8 }}><Icon name="calendar" /><span style={{ ...caption, color: "var(--on-fill)" }}>Primary 위</span></span>
    <span style={{ ...cell, color: "var(--text-disabled)" }}><Icon name="calendar" /><span style={caption}>비활성</span></span>
  </div>
);

/** 글자와 나란히 — 버튼 안 gap 6, 아이콘만 있는 버튼은 정사각형 */
export const InButtons = () => (
  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
    <button type="button" className="btn primary"><Icon name="plus" /> 새 업무</button>
    <button type="button" className="btn"><Icon name="refresh" /> 새로고침</button>
    <button type="button" className="btn ai"><Icon name="sparkle" /> AX 에게 묻기</button>
    <button type="button" className="btn icon" aria-label="검색"><Icon name="search" /></button>
    <button type="button" className="btn icon h30 ghost" aria-label="닫기"><Icon name="close" size={14} /></button>
  </div>
);
