import { IconButton } from "ax-workspace-frontend";

export default { title: "General/IconButton", component: IconButton };

const row: React.CSSProperties = { display: "flex", alignItems: "center", gap: 8 };

/** 28px 정사각. `label` 은 화면에 안 보이는 이름이다 — 없으면 읽히지 않는다 */
export const Glyphs = () => (
  <div className="surface-card" style={{ width: 420 }}>
    <div style={row}>
      <IconButton name="close" label="닫기" size={16} />
      <IconButton name="pencil" label="제목 편집" size={16} />
      <IconButton name="paperclip" label="자료 첨부" size={16} />
      <IconButton name="search" label="회의록 검색" size={16} />
      <IconButton name="reset" label="다시 불러오기" size={16} />
    </div>
  </div>
);

/** `active` — 눌린 상태(aria-pressed). 즐겨찾기처럼 「켜 둔」 것 */
export const Active = () => (
  <div className="surface-card" style={{ width: 420 }}>
    <div style={row}>
      <IconButton name="square-check" label="즐겨찾기" active size={16} />
      <IconButton name="square-check" label="즐겨찾기" size={16} />
      <IconButton name="tune" label="필터" disabled size={16} />
    </div>
  </div>
);

/** 세트에 없는 표시는 `children` 으로 — 없는 아이콘을 새로 그리지 않는다 */
export const TextGlyph = () => (
  <div className="surface-card" style={{ width: 420 }}>
    <div style={row}>
      <IconButton label="이전 달">‹</IconButton>
      <span className="t-item">2026년 9월</span>
      <IconButton label="다음 달">›</IconButton>
    </div>
  </div>
);
