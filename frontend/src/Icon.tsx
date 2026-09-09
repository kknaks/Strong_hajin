import type React from "react";

/**
 * 디자인 시스템 v2 `07 — ICON`.
 *
 * v2 는 규격만 준다 — viewBox `0 0 16 16` · stroke **1.5**(14 이하는 **1.3**) · linecap/linejoin `round` ·
 * `fill:none` · 렌더 크기 **14 / 16 / 20**, 방향 글리프만 **12**. 세트는 주지 않는다.
 *
 * 그래서 세트를 들여오지 않고 **지금 화면에 글리프가 박혀 있던 자리에 필요한 것만** 손으로 그렸다.
 * 몇 개는 v2 문서가 직접 그려 둔 path 를 그대로 옮긴 것이다(sparkle · check · calendar · list ·
 * search · empty). 장식용으로 아이콘을 늘리지 않는다 — 쓰는 자리가 생길 때 여기에 한 줄 더한다.
 */
export type IconName =
  | "close"
  | "chevron-down"
  | "chevron-right"
  | "arrow-right"
  | "arrow-up"
  | "arrow-down"
  | "sparkle"
  | "send"
  | "check"
  | "check-square"
  | "square"
  | "circle"
  | "play"
  | "list"
  | "calendar"
  | "clock"
  | "paperclip"
  | "plus"
  | "minus"
  | "home"
  | "refresh"
  | "ban"
  | "pending"
  | "alert"
  | "search"
  | "empty"
  | "filter";

const paths: Record<IconName, React.ReactNode> = {
  close: <path d="M4 4l8 8M12 4l-8 8" />,
  "chevron-down": <path d="m4 6 4 4 4-4" />,
  "chevron-right": <path d="m6 4 4 4-4 4" />,
  "arrow-right": <path d="M3 8h10M9 4l4 4-4 4" />,
  "arrow-up": <path d="M8 13V3.5M4 7.5l4-4 4 4" />,
  "arrow-down": <path d="M8 3v9.5M4 8.5l4 4 4-4" />,
  // v2 08 의 AI 버튼이 쓰는 별 — 문서의 path 그대로
  sparkle: <path d="M8 2.5 9.3 6l3.2 1.3L9.3 8.6 8 12l-1.3-3.4L3.5 7.3 6.7 6 8 2.5Z" />,
  send: <path d="M13.5 8 3 3.5 4.6 8 3 12.5 13.5 8ZM4.6 8h8.9" />,
  // v2 09 의 체크박스가 쓰는 체크 — 문서의 path 그대로
  check: <path d="m3.5 8.5 3 3 6-6" />,
  "check-square": (
    <>
      <rect height="11" rx="2" width="11" x="2.5" y="2.5" />
      <path d="m5.5 8 2 2 3.5-3.5" />
    </>
  ),
  square: <rect height="11" rx="2" width="11" x="2.5" y="2.5" />,
  circle: <circle cx="8" cy="8" r="5" />,
  play: <path d="M6.5 4.3 11.5 8l-5 3.7Z" />,
  // v2 07 의 20px 목록 글리프 — 문서의 path 그대로
  list: <path d="M3 4.5h10M3 8h10M3 11.5h6" />,
  // v2 09 의 날짜 필드 글리프 — 문서의 path 그대로
  calendar: (
    <>
      <rect height="10" rx="2" width="11" x="2.5" y="3.5" />
      <path d="M2.5 6.5h11M5.5 2v3M10.5 2v3" />
    </>
  ),
  // 시각 필드(TimeField)의 글리프 — 달력과 같은 선 두께·같은 크기로 그린 시계다
  clock: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 4.6V8l2.4 1.6" />
    </>
  ),
  paperclip: <path d="M11.8 7.3 7.2 11.9a2.4 2.4 0 0 1-3.4-3.4l5.3-5.3a1.7 1.7 0 0 1 2.4 2.4l-5.3 5.3a1 1 0 0 1-1.4-1.4l4.6-4.6" />,
  plus: <path d="M8 3.5v9M3.5 8h9" />,
  minus: <path d="M3.5 8h9" />,
  home: <path d="m2.5 7.6 5.5-4.6 5.5 4.6M4.2 7v6.5h7.6V7" />,
  refresh: <path d="M13 8a5 5 0 1 1-1.7-3.8M13.2 2.4V5.2h-2.8" />,
  ban: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="m4.1 11.9 7.8-7.8" />
    </>
  ),
  pending: <circle cx="8" cy="8" r="5.5" strokeDasharray="2.2 2.2" />,
  alert: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 5v3.6M8 10.8h.01" />
    </>
  ),
  // v2 07 의 16px 기본 글리프 — 문서의 path 그대로
  search: (
    <>
      <circle cx="7.2" cy="7.2" r="4.4" />
      <path d="m10.6 10.6 2.6 2.6" />
    </>
  ),
  // v2 10 의 빈 상태 글리프 — 문서의 path 그대로
  empty: (
    <>
      <rect height="10" rx="2" width="11" x="2.5" y="3.5" />
      <path d="M5.5 7.5h5M5.5 10h3" />
    </>
  ),
  filter: <path d="M2.8 3.5h10.4l-4 4.8v4.2l-2.4-1.6V8.3l-4-4.8Z" />,
};

export function Icon({
  name,
  size = 16,
  className,
  title,
}: {
  name: IconName;
  size?: 12 | 14 | 16 | 20;
  className?: string;
  title?: string;
}) {
  return (
    <svg
      aria-hidden={title ? undefined : true}
      className={className}
      fill="none"
      focusable="false"
      height={size}
      role={title ? "img" : undefined}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      // v2 07: 16·20 은 1.5, 14 이하는 1.3
      strokeWidth={size <= 14 ? 1.3 : 1.5}
      viewBox="0 0 16 16"
      width={size}
    >
      {title && <title>{title}</title>}
      {paths[name]}
    </svg>
  );
}
