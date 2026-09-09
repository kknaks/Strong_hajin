import { DatePicker } from "ax-workspace-frontend";
import { useEffect, useRef, useState } from "react";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/DatePicker", component: DatePicker };


/** 디자인 시스템 카드용: 마운트 직후 트리거를 눌러 열고, 카드 안에서 클릭·바깥 클릭으로 닫히면 다시 연다.
 *  실제 앱에서는 클릭으로 열고 바깥 클릭·Esc·선택으로 닫힌다 — 이 래퍼는 카드에서만 쓴다. */
function Opened({ children, minHeight = 260 }: { children: React.ReactNode; minHeight?: number }) {
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

/** 열린 상태 — 고른 날은 검정, 오늘은 테두리 */
export const Open = () => {
  const [v, setV] = useState("2026-09-30");
  return (
    <Opened minHeight={380}>
      <div className="field" style={{ width: 260 }}>
        <label htmlFor="dp-due-trigger">종료일</label>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="t-item" style={{ fontWeight: 500 }}>{v ? v.replace(/-/g, "/") : "—"}</span>
          <DatePicker id="dp-due-trigger" label="종료일" value={v} onChange={setV} />
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
      <DatePicker label="시작일" value={v} onChange={setV} min="2026-09-07" max="2026-09-18" />
    </Opened>
  );
};

/** 값 없음 — 열리면 오늘에 포커스 */
export const NoValue = () => {
  const [v, setV] = useState("");
  return (
    <Opened minHeight={380}>
      <DatePicker label="완료일" value={v} onChange={setV} />
    </Opened>
  );
};

/** 닫힌 상태 — 트리거만 */
export const Closed = () => (
  <div style={{ padding: 4 }}><DatePicker label="종료일" value="2026-09-30" onChange={() => {}} /></div>
);
