import type React from "react";

import { Icon, type IconName } from "./icons/Icon";

/**
 * 단추 셋 — `Button` · `ButtonGroup` · `IconButton` (바퀴 3a).
 *
 * **우리에 없어서 새로 만든 것이다.** 지금까지 단추는 부품이 아니라 맨 클래스(`.btn`)였고
 * 호출부 221곳에 인라인으로 박혀 있었다. 그래서 「부품 안에서 맞춘다」(D-4)를 적용할 «안» 이 없었다.
 * 여기서 그 «안» 을 만든다 — API 는 핸드오프 `scax-ui.jsx` 의 것을 그대로 따르고,
 * 스타일은 `components.css` 의 `.scax-button*` · `.scax-button-group` · `.scax-icon-button` 이 가진다.
 *
 * **아직 구 `.btn` 호출부를 이 부품으로 옮기지 않았다.** 221곳을 한 번에 고치면 이 바퀴가 화면 바퀴가
 * 된다(§7). 화면이 자기 차례에 올 때(바퀴 5·6·8) 옮겼고, 바퀴 9 가 마지막 자리까지 흡수했다 — 구 `.btn` 은 이제 없다.
 *
 * 원본에서 넓힌 곳 셋, 전부 우리 호출부가 이미 쓰던 것이라 없으면 옮길 수가 없다:
 * ① `children` — 원본은 `label` 문자열만 받는데 우리 단추는 안에 아이콘·숫자·`<b>` 를 섞어 넣는 자리가 있다.
 *    `label` 도 그대로 받는다(원본 호환). 둘 다 오면 `children` 이 이긴다.
 * ② `ariaLabel` · `title` — 아이콘만 있는 단추의 이름.
 * ③ `Button` 에 `tone="danger"` — `components.css:218` 에 `.scax-button--solid-danger` 가 실제로 있다.
 *
 * **바퀴 3c 에서 더 넓힌 것 (E-3)** — 구 `.btn` 호출부 232곳을 옮기려면 그것들이 이미 쓰던 것을
 * 받을 수 있어야 한다. 넷 다 「호출부를 비틀지 않기 위해」 연 것이고 안 넘기면 예전과 똑같이 동작한다:
 * ④ 남은 `<button>` 속성 전부(`...rest`) — `style` 5곳 · `aria-pressed` 1곳, 그리고 `Popover` 의
 *    `trigger` 렌더프롭이 넘겨 주는 `aria-expanded`/`aria-haspopup`/`aria-controls` 를 받는다(3곳).
 *    `onClick`·`disabled`·`type`·`title`·`aria-label` 은 위에 이름으로 있으므로 rest 가 덮어쓰지 않는다.
 * ⑤ `IconButton` 에 `title`·`type`·`...rest` — 같은 이유.
 * ⑥ `onClick` 을 `() => void` → `React.MouseEventHandler` 로. 이벤트 인자를 쓰는 호출부가
 *    실제로 둘 있고(`ActionCenter.tsx:95` · `meetings/MeetingListPage.tsx:306`, 둘 다 카드 안의
 *    단추라 `stopPropagation` 이 필요하다), `Popover` 트리거가 넘겨 주는 핸들러도 이 꼴이다.
 */

/**
 * `inline` 은 바퀴 9 가 더한 «글줄 속 단추» 다 (`DS-gaps` 신규 — 번호 미정). 구 `.btn.link` 자리 11곳이
 * 쓰던 것으로, 높이·여백·테두리가 없어 문장 안에 글자처럼 선다 — 다른 셋은 34px 컨트롤이라
 * 문장 흐름에 넣으면 줄 높이가 튄다. 값은 구 규칙 그대로 옮겼다(강조색 · hover 밑줄).
 */
export type ButtonVariant = "solid" | "outlined" | "text" | "inline" | "ai";
export type ButtonTone = "primary" | "neutral" | "danger";

export function Button({
  variant = "outlined",
  tone = "neutral",
  size = "md",
  label,
  iconBefore,
  block,
  disabled,
  onClick,
  type = "button",
  ariaLabel,
  title,
  className,
  children,
  ...rest
}: {
  variant?: ButtonVariant;
  tone?: ButtonTone;
  size?: "sm" | "md" | "lg";
  label?: React.ReactNode;
  iconBefore?: IconName;
  block?: boolean;
  disabled?: boolean;
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
  type?: "button" | "submit" | "reset";
  ariaLabel?: string;
  title?: string;
  className?: string;
  children?: React.ReactNode;
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "className" | "disabled" | "onClick" | "title" | "type">) {
  // 글줄 속 단추는 톤을 갈래로 두지 않는다 — 문장 안의 «누를 수 있는 말» 하나다
  // inline·ai 는 톤을 갈래로 두지 않는다 — 각각 «글줄 속 말» 과 «AX 가 하는 일» 하나다
  const classes =
    variant === "inline" || variant === "ai"
      ? ["scax-button", `scax-button--${variant}`]
      : ["scax-button", `scax-button--${variant}-${tone}`];
  if (size !== "md") classes.push(`scax-button--${size}`);
  if (block) classes.push("scax-button--block");
  if (className) classes.push(className);
  return (
    <button
      aria-label={ariaLabel}
      {...rest}
      className={classes.join(" ")}
      disabled={disabled}
      onClick={onClick}
      title={title}
      type={type}
    >
      {iconBefore ? <Icon name={iconBefore} size={16} /> : null}
      {children ?? label}
    </button>
  );
}

/** 단추를 나란히 세운다 — 안의 단추들이 폭을 똑같이 나눠 갖는다. */
export function ButtonGroup({ children }: { children?: React.ReactNode }) {
  return <div className="scax-button-group">{children}</div>;
}

/**
 * 글리프 하나짜리 단추 (28px).
 *
 * `label` 은 화면에 안 보이는 이름이다 — 아이콘만 있으니 이것이 없으면 읽히지 않는다.
 * `active` 는 눌린 상태(`aria-pressed`)이고 즐겨찾기 별처럼 «켜 둔» 것을 말한다.
 */
export function IconButton({
  name,
  children,
  size = 20,
  label,
  onClick,
  active,
  disabled,
  className,
  title,
  type = "button",
  ...rest
}: {
  /** 세트의 글리프 이름. 세트에 없는 표시(«‹›» 같은 글자)를 쓸 자리는 `children` 으로 준다. */
  name?: IconName;
  /** 바퀴 9 에서 열었다 — 달력의 앞·뒤 화살표가 글자 글리프다. 없는 아이콘을 새로 그리지 않는다. */
  children?: React.ReactNode;
  size?: 12 | 14 | 16 | 20;
  label: string;
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
  active?: boolean;
  disabled?: boolean;
  className?: string;
  title?: string;
  type?: "button" | "submit" | "reset";
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "className" | "disabled" | "onClick" | "title" | "type">) {
  const classes = ["scax-icon-button"];
  if (active) classes.push("scax-icon-button--star-on");
  if (className) classes.push(className);
  return (
    <button
      aria-label={label}
      aria-pressed={active}
      {...rest}
      className={classes.join(" ")}
      disabled={disabled}
      onClick={onClick}
      title={title}
      type={type}
    >
      {name ? <Icon name={name} size={size} /> : children}
    </button>
  );
}
