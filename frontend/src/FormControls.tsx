import type React from "react";

/**
 * 디자인 시스템 v2 `09 — FORM` 의 입력 요소들.
 *
 * 여기 있는 것은 v2 가 규격을 준 것 중 **프론트에 실제로 쓰이는 자리가 있는 것**뿐이다.
 * Radio(16)·Toggle(36×20)은 대응하는 자리가 없어 만들지 않았고, Search(h34)는 자리가 하나뿐이라
 * 컴포넌트 대신 `.search-input-box` 규격만 남겼다 — 안 쓰는 부품은 관리 비용이고, 다음 사람에게는
 * 오답 선택지가 된다(v2 `01` 의 토큰 원칙).
 */

/**
 * Checkbox · 16 × 16 · r4.
 *
 * 네이티브 input 을 지우지 않고 `appearance:none` 으로 다시 칠한다 — 키보드·스크린리더·폼 제출이
 * 전부 브라우저 것으로 남는다. 체크 글리프는 v2 문서가 그 체크박스에 쓴 path 를 그대로 옮긴 것이다.
 */
export function Checkbox({
  checked,
  onChange,
  disabled,
  id,
  children,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  id?: string;
  children?: React.ReactNode;
}) {
  return (
    <label className="checkbox">
      <CheckboxBox checked={checked} disabled={disabled} id={id} onChange={onChange} />
      {children != null && <span className="checkbox-label">{children}</span>}
    </label>
  );
}

/**
 * 체크박스의 네모 하나 — 그림만.
 *
 * `Checkbox` 와 `MultiSelect` 의 목록 항목이 **같은 그림**을 쓰도록 여기 한 벌만 둔다. 목록 항목은
 * 그 자체가 `role="option"` 인 단추라 안에 또 누를 것을 둘 수 없어서, `onChange` 를 주지 않으면
 * 읽기 전용·탭 순서 밖·AT 에 감춘 상태로 그려진다 — 누르는 일은 바깥 단추가 한다.
 */
export function CheckboxBox({
  checked,
  disabled,
  id,
  onChange,
}: {
  checked: boolean;
  disabled?: boolean;
  id?: string;
  onChange?: (checked: boolean) => void;
}) {
  const decorative = onChange === undefined;
  return (
    <span className="checkbox-box">
      <input
        aria-hidden={decorative || undefined}
        checked={checked}
        disabled={disabled}
        id={id}
        onChange={onChange ? (event) => onChange(event.target.checked) : undefined}
        readOnly={decorative}
        tabIndex={decorative ? -1 : undefined}
        type="checkbox"
      />
      <svg aria-hidden fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.2" viewBox="0 0 16 16">
        <path d="m3.5 8.5 3 3 6-6" />
      </svg>
    </span>
  );
}

/**
 * 필드 아래 한 줄.
 *
 * v2 `09`: "에러가 뜨면 헬퍼 텍스트는 에러 텍스트로 **교체**되고, 둘을 같이 쌓지 않습니다."
 * 그 규칙을 호출부의 성의에 맡기지 않고 여기서 강제한다 — 둘 다 주어도 하나만 나간다.
 */
export function FieldMessage({ error, help, id }: { error?: string | null; help?: React.ReactNode; id?: string }) {
  if (error) {
    return (
      <p className="field-error" id={id} role="alert">
        {error}
      </p>
    );
  }
  if (help) {
    return (
      <p className="field-help" id={id}>
        {help}
      </p>
    );
  }
  return null;
}
