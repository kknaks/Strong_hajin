import type React from "react";

import { Icon } from "./icons/Icon";

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
 * 네이티브 input 을 지우지 않는다 — 키보드·스크린리더·폼 제출이 전부 브라우저 것으로 남는다.
 *
 * 바퀴 3b: 그림이 새 DS 로 갔다 — `.scax-checkbox__input`(화면 밖으로 숨긴 진짜 input) +
 * `.scax-checkbox__box`(그린 네모, `input:checked + box` 로 칠해진다). 20 × 20 이 DS 규격이다.
 * DS 가 «안 가진» 셋은 우리 것을 남겼다(E-4) — **disabled 표시** · **라벨 글자와의 간격** ·
 * `onChange` 를 안 주면 읽기 전용·탭 순서 밖으로 서는 **장식용 모드**(`MultiSelect` 의 목록 항목이 쓴다).
 * 그 셋의 규칙은 `styles/components.css` 의 `.checkbox` / `.checkbox-box` 구획이 갖는다 (바퀴 9-B 에 옮겨 왔다).
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
    <label className="scax-checkbox checkbox">
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
        className="scax-checkbox__input"
        disabled={disabled}
        id={id}
        onChange={onChange ? (event) => onChange(event.target.checked) : undefined}
        readOnly={decorative}
        tabIndex={decorative ? -1 : undefined}
        type="checkbox"
      />
      <span className="scax-checkbox__box">
        <Icon name="check" size={14} />
      </span>
    </span>
  );
}

/**
 * 필드 아래 한 줄.
 *
 * v2 `09`: 「에러가 뜨면 헬퍼 텍스트는 에러 텍스트로 **교체**되고, 둘을 같이 쌓지 않습니다.」
 * 그 규칙을 호출부의 성의에 맡기지 않고 여기서 강제한다 — 둘 다 주어도 하나만 나간다.
 */
export function FieldMessage({ error, help, id }: { error?: string | null; help?: React.ReactNode; id?: string }) {
  if (error) {
    return (
      <p className="scax-field__error" id={id} role="alert">
        {error}
      </p>
    );
  }
  if (help) {
    return (
      <p className="scax-field__hint" id={id}>
        {help}
      </p>
    );
  }
  return null;
}
