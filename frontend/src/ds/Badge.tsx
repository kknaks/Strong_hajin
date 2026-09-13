import type React from "react";

/**
 * 상태·종류를 한 낱말로 다는 표 (바퀴 3a).
 *
 * **우리에 없어서 새로 만든 것이다.** 지금까지 배지는 부품이 아니라 맨 클래스(`.badge`)였고
 * 호출부 31곳에 인라인으로 박혀 있었다. API 는 핸드오프 `scax-ui.jsx` 의 `Badge({tone,variant,children})`
 * 를 따르고 스타일은 `components.css` 의 `.scax-badge*` 가 가진다.
 *
 * **아직 구 `.badge` 호출부를 옮기지 않았다** — 화면이 자기 차례에 올 때(바퀴 5·6·8) 옮긴다.
 *
 * **바퀴 3c 에서 넓힌 것 (E-3)** — 남은 `<span>` 속성 전부(`...rest`). 자료 추출 상태 배지 6곳이
 * `data-status`(CSS 가 읽는다) 와 `title`(실패 사유 툴팁)을 이미 달고 있어서, 없으면 그 자리를 못 옮긴다.
 *
 * `variant="count"` 는 안 읽은 개수를 세는 빨간 원이다 — `tone` 을 무시한다(원본과 같다).
 * 톤 다섯은 `components.css` 에 실제로 있는 것만 열었다: accent · neutral · danger · positive · info.
 */

/**
 * `outline` 은 바퀴 9 가 더한 톤이다 (`DS-gaps` G-29) — 흰 바탕에 가는 테두리.
 * 채움형 neutral 과 뜻이 다르다: 「아직 확정 아닌 값」(초안 vN · 변경 가능한 링크 · 색인 없음)을
 * 가리킨다. 구 `.badge.outline` 15자리가 쓰던 값 그대로다.
 */
export type BadgeTone = "accent" | "neutral" | "danger" | "positive" | "info" | "outline";

export function Badge({
  tone = "neutral",
  variant,
  className,
  children,
  ...rest
}: {
  tone?: BadgeTone;
  variant?: "count";
  className?: string;
  children?: React.ReactNode;
} & Omit<React.HTMLAttributes<HTMLSpanElement>, "className">) {
  const classes = ["scax-badge", variant === "count" ? "scax-badge--count" : `scax-badge--${tone}`];
  if (className) classes.push(className);
  return (
    <span {...rest} className={classes.join(" ")}>
      {children}
    </span>
  );
}
