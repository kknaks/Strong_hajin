import type React from "react";

import { Icon, type IconName } from "./Icon";
import { emptyActionLabel, emptyValue } from "./labels";

/**
 * 데이터를 보여주는 자리가 비었을 때 그리는 것.
 *
 * 디자인 시스템 v2 `10 — STATE`: 아이콘 20 `--text-disabled` · 제목 Headline 2 Bold ·
 * 설명 Label 1 Meta · 세로 gap 12 · 패널 중앙.
 *
 * v2 는 "비어 있음"과 "찾지 못함"을 구분한다 — 필터 때문에 비었으면 초기화 버튼을 함께 두고,
 * 불러오지 못한 것이면 다시 시도를 둔다. 그래서 variant 가 셋이다.
 */
export type EmptyVariant = "default" | "filter" | "error";

export function Empty({
  title,
  description,
  variant = "default",
  actionLabel,
  onAction,
  className,
  children,
}: {
  title: string;
  description?: React.ReactNode;
  variant?: EmptyVariant;
  actionLabel?: string;
  onAction?: () => void;
  className?: string;
  children?: React.ReactNode;
}) {
  // 필터·에러는 스스로 이름을 안다. 기본 variant 는 행동이 화면마다 달라 이름을 받아야 한다.
  const label = actionLabel ?? (variant === "default" ? undefined : emptyActionLabel[variant]);
  const icon: IconName = variant === "error" ? "alert" : variant === "filter" ? "filter" : "empty";
  return (
    <div
      className={["empty-state", variant === "default" ? "" : variant, className ?? ""].filter(Boolean).join(" ")}
      role={variant === "error" ? "alert" : undefined}
    >
      <span aria-hidden className="empty-icon">
        <Icon name={icon} size={20} />
      </span>
      <b>{title}</b>
      {description && <p>{description}</p>}
      {onAction && label && (
        <button className="btn" onClick={onAction} type="button">
          {label}
        </button>
      )}
      {children}
    </div>
  );
}

/** 값이 없는 칸에 남기는 것 — 공백이 아니라 대시다 (v2 `12 — TABLE`). */
export function EmptyValue() {
  return <span className="empty-value">{emptyValue}</span>;
}
