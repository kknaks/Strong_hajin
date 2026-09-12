import { Icon, Popover } from "ax-workspace-frontend";
import { useEffect, useRef, useState } from "react";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/Popover", component: Popover };


const STATES = ["전체", "시작 전", "진행 중", "완료", "취소"];

/** 디자인 시스템 카드용: 마운트 직후 트리거를 눌러 열고, 카드 안에서 클릭·바깥 클릭으로 닫히면 다시 연다.
 *  실제 앱에서는 클릭으로 열고 바깥 클릭·Esc·선택으로 닫힌다 — 이 래퍼는 카드에서만 쓴다. */
function Opened({ children, minHeight = 260 }: { children: React.ReactNode; minHeight?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const open = () => {
      const root = ref.current;
      // 패널은 포털로 body 에 선다 (DS-18) — 열렸는지는 트리거의 aria-expanded 로 묻는다
      if (!root || root.querySelector('button[aria-haspopup][aria-expanded="true"]')) return;
      root.querySelector<HTMLButtonElement>("button[aria-haspopup]")?.click();
    };
    open();
    const timer = window.setInterval(open, 400);
    return () => window.clearInterval(timer);
  }, []);
  return <div ref={ref} style={{ minHeight, padding: 4 }}>{children}</div>;
}

/** v2 05 툴바 「업무 유형 ▾」 칩 — 고르기는 팝오버 (v2 14) */
export const StatusFilter = () => {
  const [current, setCurrent] = useState("진행 중");
  return (
    <Opened>
      <Popover
        label="상태 필터"
        width={200}
        trigger={({ open, props }) => (
          <button className={open || current !== "전체" ? "filter-chip on" : "filter-chip"} {...props}>
            {current === "전체" ? "상태" : current}
            <Icon name="chevron-down" size={12} />
          </button>
        )}
      >
        {(close) =>
          STATES.map((state) => (
            <button
              key={state}
              aria-checked={state === current}
              className="popover-item"
              role="menuitemradio"
              type="button"
              onClick={() => { setCurrent(state); void close; }}
            >
              {state}
              {state === current && <Icon className="icon-check" name="check" size={14} />}
            </button>
          ))
        }
      </Popover>
    </Opened>
  );
};

/** 닫힌 상태 — 트리거만 보인다 */
export const Closed = () => (
  <div style={{ padding: 4 }}>
    <Popover
      label="정렬"
      trigger={({ props }) => (
        <button className="filter-chip" {...props}>
          종료일순 <Icon name="chevron-down" size={12} />
        </button>
      )}
    >
      {() => null}
    </Popover>
  </div>
);

/**
 * 잘리는 상자 안에서도 온전히 뜬다 (DS-18) — 드로어의 정보 카드(`overflow:hidden`)가 그 자리다.
 * 패널은 `document.body` 로 나가 `fixed` 로 서고, 좌표는 트리거의 지금 자리에서 잡는다.
 */
export const InsideClippingCard = () => (
  <Opened minHeight={220}>
    <div className="meta-grid" style={{ width: 320 }}>
      <div>
        <dt>담당 후보</dt>
        <dd>
          <Popover
            label="담당 후보"
            width={200}
            trigger={({ props }) => (
              <button className="select-trigger" {...props}>
                <span className="select-value">최유나</span>
                <Icon name="chevron-down" size={12} />
              </button>
            )}
          >
            {() =>
              ["최유나", "김낙수", "한서린"].map((name) => (
                <button className="popover-item" key={name} type="button">
                  {name}
                </button>
              ))
            }
          </Popover>
        </dd>
      </div>
    </div>
  </Opened>
);
