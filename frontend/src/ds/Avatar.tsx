import type React from "react";

/**
 * 이니셜 아바타 — 이름 첫 글자를 원에 넣는다 (바퀴 9 · `DS-gaps` G-43).
 *
 * **새 DS 에 없어서 우리가 새 DS 어휘로 만들었다.** DS 의 아바타는 `object-fit:cover` 인 «사진»
 * 전제라 글자를 받지 못하고 크기 램프도 없다. 우리 자리 11곳은 전부 사진이 아니라 첫 글자다
 * (사진이 있는 자리는 좌측 내비 하나뿐이고 그쪽은 이미 DS 것을 쓴다 — `.scax-side-nav__avatar`).
 *
 * **기하·색은 새로 디자인하지 않았다.** 구 `.avatar` 다섯 단(20·24·32·40·80)과 그 색을 그대로
 * 옮기고 이름만 `.scax-avatar` 로 바꿨다. 규칙은 `styles/components.css` 에 산다.
 *
 * 글자는 부르는 쪽이 자르지 않는다 — 이름을 통째로 주면 이 부품이 첫 글자를 뗀다. 지금까지
 * 호출부 11곳이 저마다 `.slice(0, 1)` 을 하고 있었고, 그게 «첫 글자» 라는 규칙이 열한 벌로
 * 흩어져 있다는 뜻이었다.
 *
 * 뜻을 읽히는 자리가 아니다(옆에 늘 이름이 함께 선다) — 그래서 기본이 `aria-hidden` 이다.
 * 이름만 있고 글자가 없는 자리를 위해 `label` 을 열어 두었다.
 */
export type AvatarSize = "xs" | "sm" | "md" | "lg" | "xl";

export function Avatar({
  name,
  size = "sm",
  label,
  title,
  className,
}: {
  /** 사람 이름. 첫 글자만 쓴다 — 자르는 것은 이 부품의 일이다. */
  name?: string | null;
  size?: AvatarSize;
  /** 주면 읽히는 자리가 된다. 안 주면 `aria-hidden` 이다(옆에 이름이 이미 서 있는 자리). */
  label?: string;
  title?: string;
  className?: string;
}) {
  const initial = (name ?? "").trim().slice(0, 1);
  const classes = ["scax-avatar", `scax-avatar--${size}`];
  if (className) classes.push(className);
  return (
    <span
      aria-hidden={label ? undefined : true}
      aria-label={label}
      className={classes.join(" ")}
      role={label ? "img" : undefined}
      title={title}
    >
      {initial}
    </span>
  );
}

/** 이름을 모르는 자리 — 빈 원만 선다. 글자를 지어내지 않는다. */
export function AvatarEmpty({ size = "sm", className }: { size?: AvatarSize; className?: string }) {
  return <Avatar className={className} name="" size={size} />;
}

export type AvatarProps = React.ComponentProps<typeof Avatar>;
