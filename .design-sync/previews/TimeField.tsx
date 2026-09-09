import { TimeField } from "ax-workspace-frontend";
import { useEffect, useRef, useState } from "react";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/TimeField", component: TimeField };


/** 디자인 시스템 카드용: 마운트 직후 트리거를 눌러 열고, 닫히면 다시 연다 — 카드에서만 쓴다. */
function Opened({ children, minHeight = 360 }: { children: React.ReactNode; minHeight?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const open = () => {
      const root = ref.current;
      if (!root || root.querySelector(".popover")) return;
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
        <TimeField id="tf-meeting" label="회의 시각" max="20:00" min="09:00" onChange={setV} value={v} />
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
        <TimeField label="시작 시각" onChange={setV} value={v} />
      </div>
      <div className="field">
        <span>알림 시각</span>
        <TimeField label="알림 시각" onChange={() => {}} value="" />
      </div>
      <div className="field">
        <span>마감 시각</span>
        <TimeField disabled label="마감 시각" onChange={() => {}} value="18:00" />
      </div>
    </div>
  );
};

/** 잘못된 값 — 보더가 갈아입고 헬퍼 대신 한 줄이 나간다 */
export const Invalid = () => (
  <div className="field" style={{ width: 240, padding: 4 }}>
    <span>회의 시각</span>
    <TimeField error="시각 형식이 올바르지 않습니다" label="회의 시각" onChange={() => {}} value="25:70" />
  </div>
);

/** 15분 간격 — step 은 목록만 바꾼다 */
export const FineStep = () => {
  const [v, setV] = useState("13:15");
  return (
    <div className="field" style={{ width: 240, padding: 4 }}>
      <span>통화 시각</span>
      <TimeField help="step 15" label="통화 시각" onChange={setV} step={15} value={v} />
    </div>
  );
};
