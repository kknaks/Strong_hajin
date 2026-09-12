import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type React from "react";

import { useEscape } from "./Modal";

/**
 * 고르는 자리에 얹히는 것.
 *
 * 디자인 시스템 v2 `14 — OVERLAY`: "편집은 드로어, 결정은 모달, **고르기는 팝오버**."
 * 폭 200–400 · 스크림 없음 · 트리거 바로 아래 좌측 정렬 · **8px 띄움** · 아래 공간이 모자라면 위로 ·
 * `06` 의 r12(`--radius-popover`) + `--shadow-lg`.
 *
 * 스크림이 없다는 것은 바깥이 살아 있다는 뜻이라, 닫는 길을 우리가 놔야 한다 — 바깥 클릭과 Esc 둘 다.
 *
 * **높이는 v2 가 정하지 않는다.** 그래서 "뷰포트 안에서 잘리지 않는다"를 규칙으로 삼는다 — 패널은 트리거
 * 옆에 실제로 남아 있는 자리만큼만 자라고, 내용이 그보다 길면 패널 **안에서** 스크롤한다. 뷰포트 높이만
 * 보고 자르면(예전 CSS 의 `min(60vh, 420px)`) 트리거가 아래쪽에 있을 때 패널이 화면 밖으로 나가서,
 * 스크롤할 자리 자체가 화면 밖에 남는다 — 그것이 "팝오버가 스크롤되지 않는다"의 정체였다.
 *
 * **패널은 `document.body` 로 내보내고 자리는 트리거의 지금 좌표에서 잡는다 (DS-18).** 트리거 «안» 에
 * 그리면 조상 중 `overflow` 를 가진 상자에서 잘린다 — 드로어의 정보 카드(`.meta-grid` 는
 * `overflow:hidden`)에서 담당 후보 목록과 달력이 카드 테두리에 잘리던 것이 그것이다. 포털로 내보낸 패널은
 * `fixed` 로 띄우고 좌표를 `getBoundingClientRect()` 로 직접 준다: 열릴 때, 그리고 조상이 스크롤되거나
 * 창이 바뀔 때마다 다시 잰다. 아래가 모자라면 위로 뒤집는다.
 *
 * 바깥 클릭으로 닫을 때는 트리거와 패널 **둘 다** 를 「안쪽」으로 본다 — 포털이라 패널은 트리거의 자손이
 * 아니다.
 */

/** 트리거에서 띄우는 거리 (v2 14). */
const GAP = 8;
/** 뷰포트 가장자리에 붙지 않게 남기는 여유. */
const EDGE = 8;
/** 위아래가 아무리 넓어도 이보다 크게는 열지 않는다 — 고르기는 목록을 다 보여 주는 일이 아니다. */
const MAX_HEIGHT = 420;
/** 자리가 아무리 좁아도 이보다 작게는 열지 않는다. 항목 서너 개는 보여야 고를 수 있다. */
const MIN_HEIGHT = 160;

/** 패널을 «화면 좌표» 로 어디에 얼마나 열지. `above` 는 위로 뒤집었는가다. */
type Placement = { above: boolean; maxHeight: number; top: number; left: number };

export function Popover({
  trigger,
  children,
  label,
  width = 200,
}: {
  /** 여는 단추. `open` 은 현재 상태, `toggle` 은 여닫기 — aria 는 여기서 붙여 준다. */
  trigger: (state: { open: boolean; toggle: () => void; props: React.ButtonHTMLAttributes<HTMLButtonElement> }) => React.ReactNode;
  children: (close: () => void) => React.ReactNode;
  label: string;
  width?: number;
}) {
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState<Placement>({ above: false, maxHeight: MAX_HEIGHT, top: 0, left: 0 });
  const rootRef = useRef<HTMLSpanElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      // 패널은 포털로 body 에 서 있다 — 트리거의 자손이 아니라 따로 물어야 한다
      if (rootRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  /**
   * Esc 는 **열려 있는 가장 위 한 겹만** 닫는다 — 그래서 Drawer 안의 팝오버를 닫아도 Drawer 는 그대로 있고,
   * 쓰던 것이 사라지지 않는다 (`Modal.useEscape` 의 겹 스택).
   */
  const closeToTrigger = useCallback(() => {
    setOpen(false);
    // Esc 로 닫았으면 눈은 트리거로 돌아와야 한다 — 그러지 않으면 포커스가 문서 처음으로 떨어진다.
    rootRef.current?.querySelector("button")?.focus();
  }, []);
  useEscape(closeToTrigger, open);

  /**
   * 어디로 열고 얼마나 자랄지를 트리거의 지금 자리에서 계산한다.
   *
   * 아래가 모자라고 위가 더 넓으면 위로 연다 (v2 14). 어느 쪽으로 열든 max-height 는 **그쪽에 남아 있는
   * 자리**다 — 그래서 패널은 뷰포트를 넘지 않고, 넘칠 내용은 패널 안에서 스크롤된다.
   *
   * 좌표도 여기서 준다 (DS-18) — 패널은 `fixed` 라 조상의 `overflow` 에 잘리지 않는다. 왼쪽은 트리거에
   * 맞추되 오른쪽으로 넘치면 그만큼 당겨 화면 안에 둔다.
   */
  const measure = useCallback(() => {
    const anchor = rootRef.current?.getBoundingClientRect();
    const panel = panelRef.current;
    if (!anchor || !panel) return;
    const viewport = window.innerHeight;
    const spaceBelow = viewport - anchor.bottom - GAP - EDGE;
    const spaceAbove = anchor.top - GAP - EDGE;
    // 잘리기 전의 내용 높이. max-height 가 이미 걸려 있어도 scrollHeight 는 전체를 말한다.
    const wanted = panel.scrollHeight + (panel.offsetHeight - panel.clientHeight);
    const above = wanted > spaceBelow && spaceAbove > spaceBelow;
    const room = above ? spaceAbove : spaceBelow;
    const maxHeight = Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, room));
    // 위로 열 때는 패널이 실제로 차지할 높이만큼 트리거 «위» 에서 시작한다.
    const height = Math.min(wanted, maxHeight);
    const top = above ? Math.max(EDGE, anchor.top - GAP - height) : anchor.bottom + GAP;
    const left = Math.max(EDGE, Math.min(anchor.left, window.innerWidth - width - EDGE));
    setPlacement((current) =>
      current.above === above && current.maxHeight === maxHeight && current.top === top && current.left === left
        ? current
        : { above, maxHeight, top, left },
    );
  }, [width]);

  useLayoutEffect(() => {
    if (!open) return;
    measure();
    // 트리거가 움직이면(창 크기, 조상 스크롤) 남은 자리도 달라진다. scroll 은 capture 로 받아야
    // 드로어 본문 같은 중간 스크롤 컨테이너의 스크롤도 잡힌다.
    window.addEventListener("resize", measure);
    document.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      document.removeEventListener("scroll", measure, true);
    };
  }, [measure, open]);

  const toggle = () => setOpen((value) => !value);
  return (
    <span className="popover-root" ref={rootRef}>
      {trigger({
        open,
        toggle,
        props: { "aria-expanded": open, "aria-haspopup": "true", "aria-controls": open ? panelId : undefined, onClick: toggle, type: "button" },
      })}
      {open &&
        createPortal(
          <div
            aria-label={label}
            className={placement.above ? "popover above" : "popover"}
            id={panelId}
            ref={panelRef}
            role="group"
            /* 자리는 화면 좌표로 준다 — body 로 나와 있으니 조상의 overflow 도 쌓임 맥락도 타지 않는다 (DS-18) */
            style={{ position: "fixed", top: placement.top, left: placement.left, bottom: "auto", maxHeight: placement.maxHeight, width }}
            // 안에 누를 것이 없는 팝오버(긴 글 한 덩어리)도 키보드로 스크롤할 수 있어야 한다.
            // -1 이라 탭 순서는 그대로다.
            tabIndex={-1}
          >
            {children(() => setOpen(false))}
          </div>,
          document.body,
        )}
    </span>
  );
}
