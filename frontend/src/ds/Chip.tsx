import type React from "react";

/**
 * 켜고 끄는 낱말 단추 (바퀴 3a).
 *
 * **우리에 없어서 새로 만든 것이다.** API 는 핸드오프 `scax-ui.jsx` 의 `Chip({label,on,onClick})` 을
 * 그대로 따르고, 스타일은 `components.css` 의 `.scax-chip*` 가 가진다. 새 DS 는 이것을 Category/Chip 이라 부른다.
 *
 * **`ChipToggle` 은 다른 물건이다.** 이름이 비슷하지만 그쪽은 `<label>` 안에 `<input type="checkbox">`
 * 를 넣은 «여럿 고르기» 이고, 이 `Chip` 은 `aria-pressed` 를 쓰는 단추다. 바퀴 9 에서 구 `.chip-toggle`
 * 과 `.chip-row` 를 각각 `ChipToggle` · `ChipRow` 로 옮겼다 (`DS-gaps` 신규 — 번호 미정).
 *
 * **바퀴 3c 에서 넓힌 것 (E-3)** — `className` · `children` · 남은 `<button>` 속성(`...rest`).
 * 구 `.filter-chip` 자리 하나가 `Popover` 의 `trigger` 라 `aria-expanded`/`aria-haspopup`/`aria-controls`
 * 를 받아야 한다(`MyWorkPage.tsx:385`). 안 넘기면 예전과 똑같이 동작한다.
 */
export function Chip({
  label,
  on,
  onClick,
  disabled,
  className,
  children,
  ...rest
}: {
  label?: React.ReactNode;
  on?: boolean;
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
  disabled?: boolean;
  className?: string;
  children?: React.ReactNode;
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "className" | "disabled" | "onClick">) {
  return (
    <button
      aria-pressed={on}
      {...rest}
      className={[`scax-chip${on ? " scax-chip--on" : ""}`, className ?? ""].filter(Boolean).join(" ")}
      disabled={disabled}
      onClick={onClick}
      type="button"
    >
      {children ?? label}
    </button>
  );
}

/** 칩을 한 줄로 세운다. 오른쪽 끝에 붙일 것은 `end` 로 넘긴다. */
export function ChipBar({ children, end }: { children?: React.ReactNode; end?: React.ReactNode }) {
  return (
    <div className="scax-chip-bar">
      {children}
      {end ? <div className="scax-chip-bar__end">{end}</div> : null}
    </div>
  );
}

/**
 * 칩을 줄바꿈되는 줄로 세운다 (바퀴 9 · 구 `.chip-row`).
 * `ChipBar` 와 다르다 — 그쪽은 업무 표 위 툴바라 최소 폭과 패딩을 가진다.
 */
export function ChipRow({ children, className }: { children?: React.ReactNode; className?: string }) {
  return <div className={["scax-chip-row", className ?? ""].filter(Boolean).join(" ")}>{children}</div>;
}

/**
 * 여럿 고르는 칩 (바퀴 9 · 구 `.chip-toggle`). `Chip` 과 달리 **폼 값**이다 —
 * 안에 진짜 체크박스가 있어서 이름표와 함께 서고, 키보드로도 켜고 끈다.
 */
export function ChipToggle({
  checked,
  onChange,
  disabled,
  children,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <label className={checked ? "scax-chip-toggle scax-chip-toggle--on" : "scax-chip-toggle"}>
      <input checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
      {children}
    </label>
  );
}
