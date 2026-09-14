import type { ReactNode } from "react";

import { Button, IconButton } from "../../ds/Button";
import { Icon } from "../../ds/icons/Icon";

/**
 * 한 줄 입력 한 벌 — 앞자리 조작(대상 고르기 등) · 한 줄 칸 · 던지는 단추가 한 상자로 읽힌다.
 *
 * 디자인 시스템 v2 에 이 부품이 없다. `.ax-composer`(AX 대화)는 여러 줄 textarea 라 자리가 다르다.
 * **저장 단추를 두지 않는 자리**를 위한 것이다 — 던지면 곧바로 간다.
 * `:focus-within` 은 인라인으로 못 쓰므로 테두리는 `styles/components.css` 의 `.composer` 가 갖는다.
 *
 * **던지는 자리는 두 모양이다** — 기본은 글리프 하나(좁은 자리)이고, `sendVariant="button"` 이면
 * 시안 22 처럼 «보라색 글자 단추» 가 칸 오른쪽 «밖» 에 선다. 회의 중 메모 칸이 그 모양이다:
 * 칸이 상세 폭을 거의 다 쓰는 자리에서는 글리프 하나가 끝에 붙어 있어도 무엇을 하는 자리인지 안 읽힌다.
 * 이름(`sendLabel`)은 두 모양이 같은 것을 쓴다 — 부르는 쪽이 `lib/labels` 에서 넘긴다.
 */
export function Composer({
  leading,
  value,
  onChange,
  onSubmit,
  placeholder,
  sendLabel,
  disabled = false,
  sendVariant = "glyph",
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
  /**
   * 던지는 자리의 모양. `glyph`(기본)는 칸 안의 글리프 하나, `button` 은 칸 오른쪽의 보라 글자 단추다.
   * 안 넘기면 예전과 똑같이 동작한다.
   */
  sendVariant?: "glyph" | "button";
  /** 실패 문장 — 칸 아래에 남고, 친 것은 칸에 그대로 둔다. */
  error?: string | null;
}) {
  const cannotSend = disabled || value.trim().length === 0;
  return (
    <div className="composer-shell">
      <div className="composer-row">
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
          {/* 글리프 모양일 때만 칸 «안» 에 선다 */}
          {sendVariant === "glyph" && (
            <IconButton name="send" size={14} label={sendLabel} disabled={cannotSend} onClick={onSubmit} />
          )}
        </div>
        {sendVariant === "button" && (
          <Button disabled={cannotSend} onClick={onSubmit} size="sm" tone="primary" type="button" variant="solid">
            {sendLabel}
          </Button>
        )}
      </div>
      {error && (
        <span className="scax-field__error" style={{ display: "block", marginTop: 7 }}>
          {error}
        </span>
      )}
    </div>
  );
}
