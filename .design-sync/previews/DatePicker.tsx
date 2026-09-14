import { DatePicker } from "ax-workspace-frontend";
import { useEffect, useRef, useState } from "react";

export default { title: "General/DatePicker", component: DatePicker };

/**
 * 바퀴 11 이후 이 부품은 말도 시계도 모른다 — `labels`·`weekdayNames`·`formatMonth`·`today` 가 전부 필수다.
 * 「오늘」이 어느 시간대의 오늘인지는 부르는 쪽이 안다.
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

/** 디자인 시스템 카드용: 마운트 직후 트리거를 눌러 열고, 닫히면 다시 연다 — 카드에서만 쓴다.
 *  패널 클래스는 바퀴 3a 에서 `.popover` → `.scax-popover` 가 됐다. */
function Opened({ children, minHeight = 260 }: { children: React.ReactNode; minHeight?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const open = () => {
      const root = ref.current;
      // 패널은 포털로 body 에 선다 (DS-18) — root 안에서는 절대 안 보인다.
      // 열렸는지는 트리거의 aria-expanded 로 묻는다.
      if (!root || root.querySelector('button[aria-haspopup][aria-expanded="true"]')) return;
      root.querySelector<HTMLButtonElement>("button[aria-haspopup]")?.click();
    };
    open();
    const timer = window.setInterval(open, 400);
    return () => window.clearInterval(timer);
  }, []);
  return <div ref={ref} style={{ minHeight, padding: 4 }}>{children}</div>;
}

/** 열린 상태 — 고른 날은 검정, 오늘은 테두리 */
export const Open = () => {
  const [v, setV] = useState("2026-09-30");
  return (
    <Opened minHeight={380}>
      <div className="field" style={{ width: 260 }}>
        <label htmlFor="dp-due-trigger">종료일</label>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="t-item" style={{ fontWeight: 500 }}>{v ? v.replace(/-/g, "/") : "—"}</span>
          <DatePicker {...words} id="dp-due-trigger" label="종료일" onChange={setV} value={v} />
        </div>
      </div>
    </Opened>
  );
};

/** 범위 제한 — min/max 밖은 비활성 */
export const WithRange = () => {
  const [v, setV] = useState("2026-09-10");
  return (
    <Opened minHeight={380}>
      <DatePicker {...words} label="시작일" max="2026-09-18" min="2026-09-07" onChange={setV} value={v} />
    </Opened>
  );
};

/** 값 없음 — 열리면 「오늘」에 포커스. 그 오늘은 `today` prop 이 정한다 */
export const NoValue = () => {
  const [v, setV] = useState("");
  return (
    <Opened minHeight={380}>
      <DatePicker {...words} label="완료일" onChange={setV} value={v} />
    </Opened>
  );
};

/** 닫힌 상태 — 트리거만 */
export const Closed = () => (
  <div style={{ padding: 4 }}><DatePicker {...words} label="종료일" onChange={() => {}} value="2026-09-30" /></div>
);
