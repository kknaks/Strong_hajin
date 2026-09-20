import type React from "react";

import { Icon, type IconName } from "./icons/Icon";

/**
 * 데이터를 보여주는 자리가 비었을 때 그리는 것.
 *
 * 바퀴 3a 에서 새 DS 로 갈았다 — 마크업·클래스는 핸드오프 `scax-ui.jsx` 의 `Empty`/`StatusNote` 와
 * `components.css` 의 `.scax-empty*`/`.scax-status-note*` 를 따른다.
 * **props 는 우리 것을 그대로 지켰다**(D-4): 소비처 29곳을 고치지 않는다.
 *
 * 새 DS 는 「비었다」와 「못 불러왔다」를 **다른 부품**으로 가른다 — 전자는 `Empty`(회색 원 + 아이콘),
 * 후자는 `StatusNote`(붉은 원 + `role="alert"`). 우리 `variant` 셋은 그 둘로 갈라 담는다:
 * `default`·`filter` → `.scax-empty` · `error` → `.scax-status-note`.
 * 이렇게 갈라도 부르는 쪽은 예전 그대로 `variant` 하나만 넘긴다.
 *
 * 아이콘 이름은 **지금 우리 세트의 이름**을 쓴다(바퀴 3a D-8). 새 DS 는 이 자리에 `inbox`(24) ·
 * `circle-exclamation`(24) 를 쓰는데 우리 16 그리드 세트에 그 글리프가 없다 — 아이콘 세트 교체는 바퀴 4 다.
 *
 * **G-CAL-02 (WORK-004): `icon` 을 더했다 — additive 다.** 시안 캘린더가 이 자리에 `calendar` 글리프를
 * 세운다(`calendar.v1.jsx:186`). 넘기지 않으면 예전 그대로 `variant` 가 글리프를 고르므로
 * **기존 소비처 19곳을 한 줄도 고치지 않는다** — 위 D-4 규율이 닿는 자리가 바로 「props 를 더하는 것」이다.
 * `variant="error"` 는 붉은 원 + `role="alert"` 라 「비었다」가 아니다 — 그 자리는 글리프를 안 바꾼다.
 *
 * **바퀴 11: 이 부품은 말을 모른다.** 예전에는 `variant` 가 `filter`·`error` 면 스스로 단추 이름을
 * 골라 왔다(`lib/labels` 의 `emptyActionLabel`). 이제 행동이 있으면 이름도 **반드시 함께** 받는다 —
 * 타입이 그 둘을 한 쌍으로 묶어서, 이름 없이 행동만 넘기면 컴파일이 안 된다.
 */
export type EmptyVariant = "default" | "filter" | "error";

/**
 * 행동과 그 이름은 한 쌍이다 — **이름 없이 행동만** 넘길 수 없다(그러면 부품이 이름을 지어내야 한다).
 * 행동 쪽은 조건부로 없을 수 있다(「권한이 있을 때만 만들기」 같은 자리) — 그때도 이름은 받아 둔다.
 */
type EmptyAction =
  | { onAction: (() => void) | undefined; actionLabel: string }
  | { onAction?: undefined; actionLabel?: undefined };

export function Empty({
  title,
  description,
  variant = "default",
  icon,
  actionLabel,
  onAction,
  className,
  children,
}: {
  title: string;
  description?: React.ReactNode;
  variant?: EmptyVariant;
  /** 이 자리의 글리프를 직접 고른다 (G-CAL-02). 안 주면 `variant` 가 고르던 그대로다. */
  icon?: IconName;
  className?: string;
  children?: React.ReactNode;
} & EmptyAction) {
  const label = actionLabel;
  const fallback: IconName = variant === "error" ? "circle-exclamation" : variant === "filter" ? "tune" : "inbox";
  // 못 불러온 자리(붉은 원 + alert)는 「비었다」가 아니므로 글리프를 바꾸지 않는다.
  const glyph: IconName = variant === "error" ? fallback : icon ?? fallback;
  // 못 불러온 것은 「비었다」가 아니다 — 새 DS 는 이 자리를 StatusNote 로 그린다.
  const block = variant === "error" ? "scax-status-note" : "scax-empty";
  return (
    <div
      className={[block, className ?? ""].filter(Boolean).join(" ")}
      role={variant === "error" ? "alert" : undefined}
    >
      <span aria-hidden className={`${block}__icon`}>
        {/* 새 DS 는 24 를 쓰지만 우리 Icon 은 12/14/16/20 만 받는다 — 크기 정리는 바퀴 4 */}
        <Icon name={glyph} size={20} />
      </span>
      <p className={`${block}__title`}>{title}</p>
      {description && <p className={`${block}__desc`}>{description}</p>}
      {onAction && label && (
        <button className="scax-button scax-button--outlined-neutral scax-button--sm" onClick={onAction} type="button">
          {label}
        </button>
      )}
      {children}
    </div>
  );
}

/**
 * 값이 없는 칸에 남기는 것 — 공백이 아니라 대시다 (v2 `12 — TABLE`).
 *
 * **바퀴 3a 에서 손대지 않았다**(D-3): 새 DS·핸드오프 어디에도 인라인 「값 없음」 표시가 없다
 * (`DS-gaps.md` #G-07, 사용자 컨펌 대기). 구 부품과 구 `.empty-value` 클래스를 그대로 둔다.
 *
 * 바퀴 11: 대시는 **말이 아니라 활자**다(어느 언어에서도 「—」다). 그래서 prop 으로 받지 않고
 * 이 부품이 들고 있는다 — `lib/labels` 의 `emptyValue` 는 «문자열을 만드는» 자리가 따로 쓴다.
 */
export function EmptyValue() {
  return <span className="empty-value">—</span>;
}
