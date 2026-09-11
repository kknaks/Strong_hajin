import { useEffect } from "react";

import { AssistantCharacter } from "./AssistantCharacter";
import { assistantCharacterCatalog, resolveAssistantCharacter } from "./assistantCharacterAssets";
import type { AssistantPresentationState } from "./assistantPresentation";


const previewState: AssistantPresentationState = {
  kind: "idle",
  label: "미리 보기",
  prompt: "",
};

export function AssistantCharacterPicker({
  busy,
  currentKey,
  error,
  onClose,
  onSelect,
}: {
  busy: boolean;
  currentKey: string;
  error?: string | null;
  onClose: () => void;
  onSelect: (characterKey: string) => void;
}) {
  const resolved = resolveAssistantCharacter(currentKey);
  const unsupported = resolved.key !== currentKey;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
      <section aria-label="내 AX 캐릭터" aria-modal="true" className="modal assistant-character-picker" role="dialog">
        <header className="modal-head">
          <div>
            <h3>내 AX 캐릭터</h3>
            <p>런처와 AX 답변에 표시할 캐릭터를 선택하세요.</p>
          </div>
          <button aria-label="캐릭터 선택 닫기" className="modal-close" disabled={busy} onClick={onClose} type="button">×</button>
        </header>
        {unsupported && (
          <p className="assistant-character-picker-notice" role="status">
            저장된 캐릭터를 현재 지원하지 않아 기본 캐릭터로 표시합니다. 새 항목을 선택하기 전에는 저장값을 바꾸지 않습니다.
          </p>
        )}
        {error && <p className="assistant-character-picker-error" role="alert">{error}</p>}
        <div aria-label="AX 캐릭터 목록" className="assistant-character-grid" role="radiogroup">
          {assistantCharacterCatalog.map((asset) => (
            <button
              aria-checked={resolved.key === asset.key}
              aria-label={`${asset.name} 선택`}
              className="assistant-character-option"
              disabled={busy}
              key={asset.key}
              onClick={() => onSelect(asset.key)}
              role="radio"
              type="button"
            >
              <AssistantCharacter characterKey={asset.key} size="header" state={previewState} />
              <b>{asset.name}</b>
              <small>{asset.productionApproved ? "사용 가능" : "기본 포스터"}</small>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
