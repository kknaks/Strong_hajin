import type React from "react";

import { fill16, grid16, grid24, type Fill16Name, type Grid16Name, type IconName } from "./glyphs";

export type { IconName };

/**
 * 글리프 하나를 그리는 부품 (바퀴 4 · 바퀴 10 에서 표를 `glyphs.tsx` 로 갈랐다).
 *
 * 표가 무엇을 담고 있는지는 그 파일의 머리 주석에 있다. 여기는 «어떻게 그리나» 만 안다 —
 * 그리드에 따라 viewBox 와 선 두께를 고르고, 면 글리프는 stroke 대신 fill 로 그린다.
 */

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
  const filled = name in fill16;
  const legacy = name in grid16;
  /*
   * 화면에 보이는 선 두께 = strokeWidth × (렌더 크기 ÷ viewBox 한 변).
   *
   * v2 07 의 값(16·20 → 1.5 · 14 이하 → 1.3)은 **viewBox 16 을 전제로** 쓰인 것이다.
   * 바퀴 4 가 세트를 새 DS 로 합치면서 대부분이 viewBox 24 가 됐고, 같은 숫자를 그대로 쓰면
   * 24그리드 쪽이 2/3 로 얇아진다 — 16px 렌더에서 1.0px 대 1.5px, 딱 50% 차이다.
   * 정본은 새 DS 이므로 **24그리드 값을 그대로 두고 16그리드 쪽을 16/24 만큼 줄여** 화면 두께를 맞춘다.
   * 숫자를 고정하는 것이 아니라 «보이는 굵기» 를 맞추는 것이 v2 07 이 정한 규칙이다.
   *
   * 이 분기는 임시다 — `DS-gaps` G-03 이 닫혀 남은 8종이 24그리드로 오면 `grid16` 과 함께 통째로 사라진다.
   */
  const baseStroke = size <= 14 ? 1.3 : 1.5;
  const strokeWidth = legacy ? Math.round((baseStroke * 16) / 24 * 1000) / 1000 : baseStroke;
  return (
    <svg
      aria-hidden={title ? undefined : true}
      className={className}
      fill={filled ? "currentColor" : "none"}
      focusable="false"
      height={size}
      role={title ? "img" : undefined}
      stroke={filled ? undefined : "currentColor"}
      strokeLinecap={filled ? undefined : "round"}
      strokeLinejoin={filled ? undefined : "round"}
      // 바퀴 3c: flex 줄 안에서 글리프가 찌그러지지 않는다.
      style={{ flexShrink: 0 }}
      strokeWidth={filled ? undefined : strokeWidth}
      viewBox={filled || legacy ? "0 0 16 16" : "0 0 24 24"}
      width={size}
    >
      {title && <title>{title}</title>}
      {filled
        ? fill16[name as Fill16Name]
        : legacy
          ? grid16[name as Grid16Name]
          : grid24[name as Exclude<IconName, Grid16Name | Fill16Name>]}
    </svg>
  );
}
