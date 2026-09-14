import { TimeField } from "ax-workspace-frontend";
import { useEffect, useRef, useState } from "react";

export default { title: "General/TimeField", component: TimeField };

/* 바퀴 11: `src/ds/` 안에는 한국어가 0이다 — 화면에 나가는 말은 전부 prop 이다.
   `labels`·`emptyActionLabel`·`selectLabels` 셋 다 **필수**다(목록은 `Select` 의 패널을 그대로 쓴다).
   키와 값은 `src/lib/labels.ts` 의 `timeFieldLabel` · `selectLabel` 과 같다. */
const words = {
  labels: {
    placeholder: "시각 선택",
    searchPlaceholder: "14:30",
    dash: "–",
    start: "시작 시각",
    end: "종료 시각",
    rangeInvalid: "종료가 시작보다 빠릅니다",
  },
  emptyActionLabel: "검색어 지우기",
  selectLabels: {
    placeholder: "선택",
    search: "검색",
    noMatch: "조건에 맞는 항목이 없습니다",
    clear: "지우기",
    selectAll: "전체 선택",
    clearAll: "전체 해제",
  },
};


/** 디자인 시스템 카드용: 마운트 직후 트리거를 눌러 열고, 닫히면 다시 연다 — 카드에서만 쓴다. */
function Opened({ children, minHeight = 360 }: { children: React.ReactNode; minHeight?: number }) {
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

/** 열린 상태 — 30분 간격 · 24시간 표기 · 48칸이라 검색 자동 */
export const Open = () => {
  const [v, setV] = useState("14:30");
  return (
    <Opened>
      <div className="field" style={{ width: 260 }}>
        <span>회의 시각</span>
        <TimeField {...words} id="tf-meeting" label="회의 시각" max="20:00" min="09:00" onChange={setV} value={v} />
      </div>
    </Opened>
  );
};

/** 값 있음 · 값 없음 · 비활성 — 트리거는 DateField 와 같은 h34 규격 */
export const States = () => {
  const [v, setV] = useState("09:00");
  return (
    <div style={{ display: "grid", gap: 16, width: 240, padding: 4 }}>
      <div className="field">
        <span>시작 시각</span>
        <TimeField {...words} label="시작 시각" onChange={setV} value={v} />
      </div>
      <div className="field">
        <span>알림 시각</span>
        <TimeField {...words} label="알림 시각" onChange={() => {}} value="" />
      </div>
      <div className="field">
        <span>마감 시각</span>
        <TimeField {...words} disabled label="마감 시각" onChange={() => {}} value="18:00" />
      </div>
    </div>
  );
};

/** 잘못된 값 — 보더가 갈아입고 헬퍼 대신 한 줄이 나간다 */
export const Invalid = () => (
  <div className="field" style={{ width: 240, padding: 4 }}>
    <span>회의 시각</span>
    <TimeField {...words} error="시각 형식이 올바르지 않습니다" label="회의 시각" onChange={() => {}} value="25:70" />
  </div>
);

/** 15분 간격 — step 은 목록만 바꾼다 */
export const FineStep = () => {
  const [v, setV] = useState("13:15");
  return (
    <div className="field" style={{ width: 240, padding: 4 }}>
      <span>통화 시각</span>
      <TimeField {...words} help="step 15" label="통화 시각" onChange={setV} step={15} value={v} />
    </div>
  );
};
