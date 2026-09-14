import { Button } from "./Button";
import { Icon } from "./icons/Icon";
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
 *
 * **이번 바퀴 — 안의 «짜임» 까지 시안으로 갔다 (시안 12).**
 * 예전에는 안내 한 줄 · 고르는 단추 · 한도 한 줄이 세로로 쌓였고, 고른 파일 목록은 이 칸 «밖» 에
 * 따로 섰다. 시안은 그 둘을 뒤집는다:
 *   · 머리 한 줄(`.scax-dropzone__head`) = 안내 문구 + 오른쪽 끝 보라 단추. DS 에 규칙이 이미 있었고
 *     쓰는 마크업만 없었다 (`components.css` 의 `__head` · `__hint` · `__cursor`).
 *   · 고른 파일 목록은 **이 칸 안**에 들어온다 — `children` 으로 받는다.
 * 끌어다 놓는 동안에는 칸 전체가 연보라 바닥 + 보라 점선(`--over`)이고, 오른쪽 위에 집는 표시
 * (`__cursor`)가 뜬다 — 이 셋 다 DS 규칙이다. 새 색도 새 값도 만들지 않았다.
 *
 * 핸드오프 것은 `onAdd()` 만 부르는 시안이라 실제 파일을 안 받는다 — 우리 `onFiles(File[])` 를 지켰다.
 */
export function DropZone({
  hint,
  drop,
  pickLabel,
  accept,
  onFiles,
  disabled = false,
  children,
}: {
  /** 무엇을 받는지 한 줄 — 「한 건당 20MB · PDF · Markdown」 같은 것. 계약이 말하는 사실이다. */
  hint: ReactNode;
  /** 머리 한 줄의 안내 문구 — 「첨부할 파일을 끌어다 놓거나 추가하세요」. */
  drop: ReactNode;
  pickLabel: string;
  /** `<input accept>` 값. 브라우저 고르기 창을 좁히기만 한다. */
  accept?: string;
  onFiles: (files: File[]) => void;
  disabled?: boolean;
  /** 고른 파일 목록 — 시안대로 이 칸 «안» 에 선다. */
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
      <div className="scax-dropzone__head">
        <span className="scax-dropzone__hint">{drop}</span>
        {/* 시안의 이 단추는 보라 면이다 — 칸 안에서 유일하게 누르는 자리라 눈이 갈 곳이 하나다 */}
        <Button size="sm" variant="solid" tone="primary" disabled={disabled} onClick={() => input.current?.click()} type="button">
          {pickLabel}
        </Button>
      </div>
      {/* 끌어다 놓는 동안 뜨는 집는 표시 — 시안 12 의 「드롭 다운」 상태다 */}
      {over && (
        <span aria-hidden className="scax-dropzone__cursor">
          <Icon name="document" size={20} />
        </span>
      )}
      {/* 무엇을 받는지 — 시안에는 없지만 계약이 말하는 사실이라 지운다면 거짓이 된다 */}
      <span className="t-meta" style={{ fontSize: 12 }}>
        {hint}
      </span>
      {children}
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
