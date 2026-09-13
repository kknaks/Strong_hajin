import type { ReactNode } from "react";

import { IconButton } from "../../ds/Button";
import { Icon } from "../../ds/icons/Icon";

/**
 * 한 줄 입력 한 벌 — 앞자리 조작(대상 고르기 등) · 한 줄 칸 · 던지는 단추가 한 상자로 읽힌다.
 *
 * 디자인 시스템 v2 에 이 부품이 없다. `.ax-composer`(AX 대화)는 여러 줄 textarea 라 자리가 다르다.
 * **저장 단추를 두지 않는 자리**를 위한 것이다 — 던지면 곧바로 간다.
 * `:focus-within` 은 인라인으로 못 쓰므로 테두리는 `styles/components.css` 의 `.composer` 가 갖는다.
 */
export function Composer({
  leading,
  value,
  onChange,
  onSubmit,
  placeholder,
  sendLabel,
  disabled = false,
  error,
}: {
  /** 칸 앞에 서는 조작 — 없으면 구분선도 서지 않는다. */
  leading?: ReactNode;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  placeholder: string;
  /** 던지는 단추의 접근 이름. 아이콘만 서므로 이름이 곧 그 단추다. */
  sendLabel: string;
  disabled?: boolean;
  /** 실패 문장 — 칸 아래에 남고, 친 것은 칸에 그대로 둔다. */
  error?: string | null;
}) {
  return (
    <div>
      <div className="composer">
        {leading}
        {leading && <span aria-hidden className="composer-divider" />}
        <input
          aria-label={placeholder}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") onSubmit();
          }}
          placeholder={placeholder}
          type="text"
          value={value}
        />
        <IconButton name="send" size={14} label={sendLabel} disabled={disabled || value.trim().length === 0} onClick={onSubmit} />
      </div>
      {error && (
        <span className="scax-field__error" style={{ display: "block", marginTop: 7 }}>
          {error}
        </span>
      )}
    </div>
  );
}
