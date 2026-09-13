import { Button } from "./Button";
import { useRef, useState, type ReactNode } from "react";

/**
 * 파일을 끌어다 놓거나 골라 받는 자리.
 *
 * 디자인 시스템 v2 에 이 부품이 없다 — 파일을 받는 화면이 이 제품에 여럿인데(회의 자료 · 업무 자료 ·
 * 요청 증빙) 저마다 다르게 생겨 있었다. 무엇을 받는지(`hint`)와 고르는 말(`pickLabel`)만 갈린다.
 *
 * **받는 것은 이 부품이 판단하지 않는다** — 크기·형식은 부르는 쪽과 서버가 정한다.
 *
 * 바퀴 3b: 껍데기가 새 DS 의 `.scax-dropzone` / `--over` 로 갔다(핸드오프 `work-modal.jsx` 의 `DropZone`).
 * 안의 구성(고르는 단추 · 무엇을 받는지 한 줄 · 숨은 `<input type=file>`)은 우리 것 그대로다.
 * 핸드오프 것은 `onAdd()` 만 부르는 시안이라 실제 파일을 안 받는다 — 우리 `onFiles(File[])` 를 지켰다.
 */
export function DropZone({
  hint,
  pickLabel,
  accept,
  onFiles,
  disabled = false,
  children,
}: {
  /** 무엇을 받는지 한 줄 — 「한 건당 20MB · PDF · Markdown」 같은 것. */
  hint: ReactNode;
  pickLabel: string;
  /** `<input accept>` 값. 브라우저 고르기 창을 좁히기만 한다. */
  accept?: string;
  onFiles: (files: File[]) => void;
  disabled?: boolean;
  /** 고르는 단추 위에 서는 말. */
  children?: ReactNode;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  function take(list: FileList | null) {
    if (!list || list.length === 0) return;
    onFiles(Array.from(list));
  }

  return (
    <div
      className={over ? "scax-dropzone scax-dropzone--over" : "scax-dropzone"}
      onDragLeave={() => setOver(false)}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        if (!disabled) take(event.dataTransfer.files);
      }}
    >
      {children}
      <Button size="sm" disabled={disabled} onClick={() => input.current?.click()} type="button">
        {pickLabel}
      </Button>
      <span className="t-meta" style={{ fontSize: 12 }}>
        {hint}
      </span>
      <input
        accept={accept}
        aria-label={pickLabel}
        hidden
        multiple
        onChange={(event) => {
          take(event.target.files);
          // 같은 파일을 다시 고를 수 있어야 한다 — 값을 비우지 않으면 change 가 안 온다.
          event.target.value = "";
        }}
        ref={input}
        type="file"
      />
    </div>
  );
}
