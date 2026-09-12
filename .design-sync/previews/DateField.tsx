import { DateField } from "ax-workspace-frontend";
import { useState } from "react";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/DateField", component: DateField };


const box: React.CSSProperties = { width: 260 };

/** 칸 하나 — 값(ISO)이 왼쪽, 달력 아이콘이 칸 안 오른쪽. 누르면 우리 DatePicker 가 열린다 (DS-17) */
export const WithValue = () => {
  const [v, setV] = useState("2026-09-30");
  return <div style={box}><DateField id="df-end" label="종료일" value={v} onChange={setV} /></div>;
};

/** 비어 있음 — 자리표시는 YYYY-MM-DD */
export const Empty = () => {
  const [v, setV] = useState("");
  return <div style={box}><DateField id="df-start" label="시작일" value={v} onChange={setV} /></div>;
};

/** 비활성 — 달력이 열리지 않는다 */
export const Disabled = () => (
  <div style={box}><DateField id="df-locked" label="완료일" value="2026-09-04" onChange={() => {}} disabled /></div>
);

/** 기간 — 레이블은 스크린리더에만 */
export const Range = () => {
  const [s, setS] = useState("2026-09-08");
  const [e, setE] = useState("2026-09-12");
  return (
    <div className="field" style={{ width: 400 }}>
      <span>기간</span>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <div style={{ flex: 1 }}><DateField id="df-r-s" label="시작일" value={s} onChange={setS} hideLabel /></div>
        <span className="t-meta">–</span>
        <div style={{ flex: 1 }}><DateField id="df-r-e" label="종료일" value={e} onChange={setE} hideLabel /></div>
      </div>
    </div>
  );
};
