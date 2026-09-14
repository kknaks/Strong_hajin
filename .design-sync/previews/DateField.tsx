import { DateField } from "ax-workspace-frontend";
import { useState } from "react";

export default { title: "General/DateField", component: DateField };

/**
 * 바퀴 11 이후 이 부품은 **말도 시계도 모른다** — `today`·`labels`·`weekdayNames`·`formatMonth` 를
 * 아래 `DatePicker` 로 그대로 흘려보내므로 넷 다 필수다.
 */
/* 바퀴 11: `src/ds/` 안에는 한국어가 0이다 — 화면에 나가는 말은 전부 prop 이다.
   앱에서는 `src/lib/labels.ts` 가 이 자리를 맡지만 번들 엔트리는 디자인 부품만 내보내므로
   프리뷰가 **호출부로서** 같은 값을 들고 있어야 한다. 키와 값은 그 파일과 같다. */
const words = {
  /** 프리뷰의 「오늘」은 고정한다 — 리뷰 시트가 시계를 고정해 찍기 때문에 실제 시계에 기대면 카드마다 다른 달이 열린다 */
  today: "2026-09-14",
  labels: { open: "달력 열기", previousMonth: "이전 달", nextMonth: "다음 달", clear: "지우기", today: "오늘" },
  weekdayNames: ["일", "월", "화", "수", "목", "금", "토"],
  // `lib/labels.ts` 의 `formatMonthLong` 과 같다 — month 는 **1-based** 다(+1 하지 않는다).
  formatMonth: (year: number, month: number) => `${year}년 ${month}월`,
};

const box: React.CSSProperties = { width: 260 };

/** 칸 하나 — 값(ISO)이 왼쪽, 달력 글리프가 칸 안 오른쪽. 누르면 우리 DatePicker 가 열린다 (DS-17) */
export const WithValue = () => {
  const [v, setV] = useState("2026-09-30");
  return <div style={box}><DateField {...words} id="df-end" label="종료일" onChange={setV} value={v} /></div>;
};

/** 비어 있음 — 자리표시는 YYYY-MM-DD */
export const Empty = () => {
  const [v, setV] = useState("");
  return <div style={box}><DateField {...words} id="df-start" label="시작일" onChange={setV} value={v} /></div>;
};

/** 비활성 — 달력이 열리지 않는다 */
export const Disabled = () => (
  <div style={box}><DateField {...words} disabled id="df-locked" label="완료일" onChange={() => {}} value="2026-09-04" /></div>
);

/** 화면 구분자 · 필수 표시 — 값 자체는 언제나 ISO 로 오간다 */
export const DottedRequired = () => {
  const [v, setV] = useState("2026-09-30");
  return (
    <div style={box}>
      <DateField {...words} displaySeparator="." id="df-dot" label="회의일" onChange={setV} pickerIcon="chevron-down" required value={v} />
    </div>
  );
};

/** 기간 — 이름표는 스크린리더에만 */
export const Range = () => {
  const [s, setS] = useState("2026-09-08");
  const [e, setE] = useState("2026-09-12");
  return (
    <div className="field" style={{ width: 400 }}>
      <span>기간</span>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <div style={{ flex: 1 }}><DateField {...words} hideLabel id="df-r-s" label="시작일" onChange={setS} value={s} /></div>
        <span className="t-meta">–</span>
        <div style={{ flex: 1 }}><DateField {...words} hideLabel id="df-r-e" label="종료일" onChange={setE} value={e} /></div>
      </div>
    </div>
  );
};
