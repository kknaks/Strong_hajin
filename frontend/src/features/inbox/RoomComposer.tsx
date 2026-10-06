import { useRef, useState } from "react";

import { IconButton } from "../../ds/Button";
import { FileList } from "../../ds/FileList";
import { Icon, type IconName } from "../../ds/icons/Icon";
import { inboxScreen as copy } from "../../lib/labels";
import { extOf, fmtSize, SLACK_FILE_LIMIT_BYTES } from "./inboxModel";

/**
 * 슬랙 입력창 — 채널 맨 아래 · 스레드 패널 아래 둘 다 이것 (시안 `SlackComposer` · DC-3).
 *
 * 서식 막대(굵게·기울임·링크·목록·코드)는 슬랙 mrkdwn 기호로 고른 글을 감싼다 · 왼쪽 「+」 = 첨부 창 · 오른쪽 보내기 ·
 * Enter 보내기 / Shift+Enter 줄바꿈. 파일을 입력창 위로 끌어오면 칸 전체가 DS DropZone 의 `--over` 모양이 되고,
 * 고른 파일은 칸 안에 DS FileList 로 선다. 슬랙 보내기 한도는 **파일 하나당 50MB**(OQ-807) — 넘는 파일이 있으면 막는다.
 */

const FORMAT_TOOLS: Array<{ id: "b" | "i" | "link" | "list" | "code"; glyph?: string; icon?: IconName; wrap: [string, string] }> = [
  { id: "b", glyph: "B", wrap: ["*", "*"] },
  { id: "i", glyph: "I", wrap: ["_", "_"] },
  { id: "link", icon: "link", wrap: ["<", ">"] },
  { id: "list", icon: "list-category", wrap: ["• ", ""] },
  { id: "code", glyph: "</>", wrap: ["`", "`"] },
];

type Staged = { key: string; file: File };

let stageSeq = 0;
const stage = (files: File[]): Staged[] =>
  files.map((file) => {
    stageSeq += 1;
    return { key: `f${stageSeq}`, file };
  });

export function RoomComposer({ placeholder, onSend, disabled = false }: { placeholder: string; onSend: (text: string, files: File[]) => void; disabled?: boolean }) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState<Staged[]>([]);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const tooBig = files.some((item) => item.file.size > SLACK_FILE_LIMIT_BYTES);
  const canSend = !disabled && !tooBig && (text.trim() !== "" || files.length > 0);

  const add = (list: File[]) => setFiles((current) => current.concat(stage(list)));
  const send = () => {
    if (!canSend) return;
    onSend(text.trim(), files.map((item) => item.file));
    setText("");
    setFiles([]);
  };
  const format = (wrap: [string, string]) => {
    const area = input.current;
    if (!area) return;
    const start = area.selectionStart ?? text.length;
    const end = area.selectionEnd ?? text.length;
    const next = `${text.slice(0, start)}${wrap[0]}${text.slice(start, end)}${wrap[1]}${text.slice(end)}`;
    setText(next);
    window.requestAnimationFrame(() => {
      area.focus();
      area.setSelectionRange(start + wrap[0].length, end + wrap[0].length);
    });
  };

  return (
    <div
      className={`scax-composer-box${over ? " scax-dropzone scax-dropzone--over" : ""}`}
      onDragLeave={() => setOver(false)}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        if (!disabled && event.dataTransfer.files.length) add(Array.from(event.dataTransfer.files));
      }}
    >
      {over ? (
        <div className="scax-composer-box__drop">
          <span aria-hidden className="scax-dropzone__cursor">
            <Icon name="document" size={20} />
          </span>
          <span className="scax-composer-box__drop-text">{copy.dropHere}</span>
        </div>
      ) : null}
      <div aria-label={copy.formatBar} className="scax-composer-box__tools" role="toolbar">
        {FORMAT_TOOLS.map((tool) => (
          <button
            aria-label={copy.format[tool.id]}
            className={`scax-composer-box__tool scax-composer-box__tool--${tool.id}`}
            disabled={disabled}
            key={tool.id}
            onClick={() => format(tool.wrap)}
            title={copy.format[tool.id]}
            type="button"
          >
            {tool.icon ? <Icon name={tool.icon} size={16} /> : tool.glyph}
          </button>
        ))}
      </div>
      <textarea
        aria-label={placeholder}
        className="scax-composer-box__input"
        disabled={disabled}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            send();
          }
        }}
        placeholder={placeholder}
        ref={input}
        rows={2}
        value={text}
      />
      {files.length ? (
        <div className="scax-composer-box__files">
          <FileList
            label={copy.outgoing}
            rows={files.map((item) => ({
              key: item.key,
              name: item.file.name,
              size: fmtSize(item.file.size),
              icon: /^(png|jpe?g|gif|webp)$/.test(extOf(item.file.name)) ? "blank" : "document",
              reason: item.file.size > SLACK_FILE_LIMIT_BYTES ? copy.overSlackFile : null,
              onRemove: () => setFiles((current) => current.filter((row) => row.key !== item.key)),
              removeLabel: copy.removeAddress(item.file.name),
            }))}
          />
        </div>
      ) : null}
      <div className="scax-composer-box__foot">
        <IconButton disabled={disabled} label={copy.attachFile} name="plus" onClick={() => picker.current?.click()} />
        <span className="scax-composer-box__hint">{copy.composerHint}</span>
        <button aria-label={copy.send} className="scax-composer-box__send" disabled={!canSend} onClick={send} type="button">
          <Icon name="send" size={16} />
        </button>
      </div>
      <input
        aria-label={copy.pickFiles}
        hidden
        multiple
        onChange={(event) => {
          if (event.target.files && event.target.files.length) add(Array.from(event.target.files));
          event.target.value = "";
        }}
        ref={picker}
        type="file"
      />
    </div>
  );
}
