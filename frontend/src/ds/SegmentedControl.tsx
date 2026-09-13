import type React from "react";

/**
 * 하나를 고르는 컨트롤 둘 — `SegmentedControl`(칸 안에서) · `Tabs`(면을 가르며) (바퀴 3a).
 *
 * **우리에 없어서 새로 만든 것이다.** 지금까지 둘 다 부품이 아니라 맨 클래스(`.segmented` · `.page-tabs`)로
 * 호출부에 인라인으로 박혀 있었다. API 는 핸드오프 `scax-ui.jsx` 의 것을 그대로 따르고,
 * 스타일은 `components.css` 의 `.scax-segmented*` · `.scax-tabs*` 가 가진다.
 *
 * 원본에서 넓힌 곳 하나 — 항목에 **`disabled`** 를 열었다. 관계 탐색 화면이 그래프를 불러오는 동안
 * 표현 수준을 못 바꾸게 막고 있어서(`RelationGraphPage.tsx:174`), 이것이 없으면 그 자리를 옮길 수가 없다.
 * 원본은 `{value,label}` 뿐이고 `disabled` 를 안 주면 예전과 똑같이 동작한다.
 */

export type ChoiceOption<T extends string> = {
  value: T;
  label: React.ReactNode;
  disabled?: boolean;
};

/** 칸 안에서 고른다 — 배경이 있는 작은 띠. 보기 방식·기간처럼 «같은 것을 다르게 보는» 자리. */
export function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  value: T;
  options: ReadonlyArray<ChoiceOption<T>>;
  onChange: (value: T) => void;
  ariaLabel: string;
}) {
  return (
    <div aria-label={ariaLabel} className="scax-segmented" role="tablist">
      {options.map((option) => (
        <button
          aria-selected={option.value === value}
          className={`scax-segmented__item${option.value === value ? " scax-segmented__item--on" : ""}`}
          disabled={option.disabled}
          key={option.value}
          onClick={() => onChange(option.value)}
          role="tab"
          type="button"
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/**
 * 면을 가르며 고른다 — 밑줄이 서는 페이지 단위 탭.
 *
 * `.scax-tabs` 는 `min-width` 880 · 좌우 여백 24 · 아래 경계선을 스스로 갖는 «본문 칸 폭» 부품이다.
 * 그래서 페이지 머리 안쪽에 끼워 넣던 구 `.page-tabs` 자리와 크기가 다르다 — 자리 옮기기는 그 화면의
 * 바퀴(5)가 레이아웃과 함께 한다.
 */
export function Tabs<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  value: T;
  options: ReadonlyArray<ChoiceOption<T>>;
  onChange: (value: T) => void;
  ariaLabel: string;
}) {
  return (
    <div aria-label={ariaLabel} className="scax-tabs" role="tablist">
      {options.map((option) => (
        <button
          aria-selected={option.value === value}
          className={`scax-tabs__item${option.value === value ? " scax-tabs__item--on" : ""}`}
          disabled={option.disabled}
          key={option.value}
          onClick={() => onChange(option.value)}
          role="tab"
          type="button"
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
