import { useState, type ReactNode } from "react";

import { Icon } from "../../ds/icons/Icon";
import { inboxScreen as copy } from "../../lib/labels";
import type { InboxAttachment } from "../../lib/viewModels";
import { fileTypeOf, fmtSize, isPdf, type Reaction, type Unfurl } from "./inboxModel";

/**
 * 메시지함 첨부 부품 — 메일·슬랙·카톡이 같이 쓴다 (시안 `inbox.v1.jsx` 의 FileCard ~ ExpiredCard).
 *
 * 받기는 **API 중계**다(§2.2 · D-29): 메일·슬랙은 누를 때 서버가 그 사람 토큰으로 받아 넘기고, 카톡은 저장본이다.
 * 그래서 받기 단추는 우리 `/api/inbox/…/attachments/{aid}` 주소를 가리키는 링크일 뿐 — 여기서 fetch 하지 않는다.
 * 데스크톱 셸은 같은 origin `/api/` 이동을 가로채 다운로드 폴더에 저장한다(SPEC-006 U-5).
 */

const FILE_TYPE: Record<string, { label: string; mark: string }> = {
  pdf: { label: "PDF", mark: "PDF" },
  md: { label: "Markdown", mark: "MD" },
  xlsx: { label: "Excel 스프레드시트", mark: "XLS" },
  docx: { label: "Word 문서", mark: "DOC" },
  zip: { label: "ZIP 압축 파일", mark: "ZIP" },
  png: { label: "PNG 이미지", mark: "IMG" },
  jpg: { label: "JPEG 이미지", mark: "IMG" },
};

const fileType = (type: string) => FILE_TYPE[type] ?? { label: type.toUpperCase(), mark: type.toUpperCase().slice(0, 3) };

export function FileMark({ type, size = "md" }: { type: string; size?: "md" | "sm" }) {
  return (
    <span aria-hidden className={`scax-fmark scax-fmark--${type} scax-fmark--size-${size}`}>
      {fileType(type).mark}
    </span>
  );
}

/** 받기 — 아이콘 단추 모양의 링크. 주소가 없으면(보낸 답장의 첨부처럼 받을 길이 없는 것) 그리지 않는다. */
function DownloadLink({ href, name }: { href?: string | null; name: string }) {
  if (!href) return null;
  return (
    <a aria-label={copy.download(name)} className="scax-icon-button" download={name} href={href} rel="noreferrer" target="_blank" title={copy.download(name)}>
      <Icon name="arrow-down" size={20} />
    </a>
  );
}

/** 파일 카드 — 유형 색 표식 · 굵은 이름(말줄임) · 아래 유형명·크기 · 받기. */
export function FileCard({ name, mime, size, href }: { name: string; mime?: string | null; size?: number | null; href?: string | null }) {
  const type = fileTypeOf(name, mime);
  const sizeText = fmtSize(size);
  return (
    <div className="scax-fcard" title={name}>
      <FileMark type={type} />
      <span className="scax-fcard__text">
        <span className="scax-fcard__name">{name}</span>
        <span className="scax-fcard__type">
          {fileType(type).label}
          {sizeText ? ` · ${sizeText}` : ""}
        </span>
      </span>
      <DownloadLink href={href} name={name} />
    </div>
  );
}

/** 접히는 머리 — 「3개의 첨부 파일 ▾」·「PDF ▾」·「파일명 ▾」 */
function FoldHead({ label, open, onToggle, end }: { label: string; open: boolean; onToggle: () => void; end?: ReactNode }) {
  return (
    <div className="scax-fold">
      <button aria-expanded={open} className="scax-fold__toggle" onClick={onToggle} type="button">
        <span className="scax-fold__label">{label}</span>
        <Icon name={open ? "chevron-down" : "chevron-right"} size={16} />
      </button>
      {end ? <span aria-hidden className="scax-fold__sep" /> : null}
      {end}
    </div>
  );
}

/** 「모두 다운로드」 — 받기 링크를 차례로 누른다(하나씩 내려받는다). */
function downloadAll(list: Array<{ name: string; href: string }>) {
  list.forEach((item, index) => {
    window.setTimeout(() => {
      const anchor = document.createElement("a");
      anchor.href = item.href;
      anchor.download = item.name;
      anchor.rel = "noreferrer";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    }, index * 300);
  });
}

/** `href` = 원본(누르기·받기) · `thumb` = 대화 안에 그리는 주소(썸네일이 있으면 그것, 없으면 원본). */
type Linked = InboxAttachment & { href: string | null; thumb: string | null };

function FileGroup({ files }: { files: Linked[] }) {
  const [open, setOpen] = useState(true);
  const linked = files.filter((file): file is Linked & { href: string } => Boolean(file.href));
  return (
    <div className="scax-attach">
      <FoldHead
        end={
          linked.length ? (
            <button className="scax-fold__action scax-fold__toggle" onClick={() => downloadAll(linked)} type="button">
              <Icon name="arrow-down" size={16} />
              {copy.downloadAll}
            </button>
          ) : null
        }
        label={copy.filesCount(files.length)}
        onToggle={() => setOpen((value) => !value)}
        open={open}
      />
      {open ? (
        <div className="scax-fcard-row">
          {files.map((file) => (
            <FileCard href={file.href} key={file.aid} mime={file.mime} name={file.name} size={file.size} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * PDF 하나 — 큰 카드의 머리(표식·이름·크기·받기).
 * 시안의 「첫 페이지 미리보기」는 서버가 그림을 만들어 주지 않아 그리지 않는다(DS-gaps — 미리보기 이미지 없음).
 */
function PdfBlock({ file }: { file: Linked }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="scax-attach">
      <FoldHead label={copy.pdf} onToggle={() => setOpen((value) => !value)} open={open} />
      {open ? (
        <div className="scax-pdf">
          <div className="scax-pdf__head">
            <FileMark type="pdf" />
            <span className="scax-fcard__text">
              <span className="scax-fcard__name">{file.name}</span>
              <span className="scax-fcard__type">
                {copy.pdf}
                {file.size ? ` · ${fmtSize(file.size)}` : ""}
              </span>
            </span>
            <DownloadLink href={file.href} name={file.name} />
          </div>
        </div>
      ) : null}
    </div>
  );
}

/**
 * 그림 — 받은 이미지가 선다(중계·저장본 주소). 누르면 새 탭에서 **원본**을 크게 본다.
 * `src` 는 그리는 주소(방 첨부는 썸네일), `href` 는 누를 때 여는 원본 — 주지 않으면 `src` 와 같다.
 */
export function Thumb({ src, href, alt, size = "lg" }: { src: string | null; href?: string | null; alt: string; size?: "lg" | "md" | "sm" | "cell" }) {
  return (
    <span className={`scax-thumb scax-thumb--${size}`}>
      {src ? (
        <a className="scax-thumb__link" href={href ?? src} rel="noreferrer" target="_blank">
          <img alt={alt} className="scax-thumb__img" loading="lazy" src={src} />
        </a>
      ) : (
        <Icon name="blank" size={size === "lg" ? 24 : 20} />
      )}
    </span>
  );
}

function ImageBlock({ file }: { file: Linked }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="scax-attach">
      <FoldHead label={file.name} onToggle={() => setOpen((value) => !value)} open={open} />
      {open ? <Thumb alt={file.name} href={file.href} src={file.thumb} /> : null}
    </div>
  );
}

/** 앨범 — 여러 장을 격자로. 다섯 장째부터는 넷째 칸에 「+N」. */
function AlbumBlock({ files }: { files: Linked[] }) {
  const [open, setOpen] = useState(true);
  const shown = files.slice(0, 4);
  const rest = files.length - shown.length;
  return (
    <div className="scax-attach">
      <FoldHead label={copy.photos(files.length)} onToggle={() => setOpen((value) => !value)} open={open} />
      {open ? (
        <div className={`scax-album scax-album--${Math.min(files.length, 4)}`}>
          {shown.map((file, index) => (
            <span className="scax-album__cell" key={file.aid}>
              <Thumb alt={file.name} href={file.href} size="cell" src={file.thumb} />
              {index === shown.length - 1 && rest > 0 ? <span className="scax-album__more">+{rest}</span> : null}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** 동영상·음성 — 받지 않는다(D-31). 회색 칩으로 무엇이 왔는지만. */
function MediaChip({ kind }: { kind: "video" | "voice" }) {
  return (
    <div className="scax-attach">
      <span className={`scax-media-chip scax-media-chip--${kind}`}>
        <Icon name={kind === "video" ? "play" : "pending"} size={16} />
        <span className="scax-media-chip__label">{kind === "video" ? copy.video : copy.voice}</span>
        <span className="scax-media-chip__note">{copy.notFetched}</span>
      </span>
    </div>
  );
}

/** 만료됨 · 너무 큼 — 낼 수 없는 카톡 첨부. 받기가 없다. */
function ExpiredCard({ file }: { file: InboxAttachment }) {
  const photo = file.kind === "image" || file.kind === "album";
  return (
    <div className="scax-attach">
      <div className="scax-fcard scax-fcard--expired" title={file.name}>
        <span aria-hidden className="scax-fmark scax-fmark--expired scax-fmark--size-md">
          {photo ? "IMG" : "FILE"}
        </span>
        <span className="scax-fcard__text">
          <span className="scax-fcard__name">{file.name || (photo ? copy.photoKind : copy.fileKind)}</span>
          <span className="scax-fcard__type">
            {photo ? copy.photoKind : copy.fileKind} · {file.state === "too_large" ? copy.tooLarge : copy.expired}
          </span>
        </span>
      </div>
    </div>
  );
}

/** 한 메시지의 첨부 전부 — 종류별로 시안의 부품에 나눠 그린다. */
export function AttachmentList({
  attachments,
  hrefOf,
  thumbOf,
}: {
  attachments: InboxAttachment[];
  hrefOf: (aid: string) => string;
  /** 이미지 미리보기 주소(썸네일). 없으면 원본 주소로 그린다. */
  thumbOf?: (aid: string) => string;
}) {
  if (!attachments.length) return null;
  const blocked = (item: InboxAttachment) => item.state === "expired" || item.state === "too_large";
  const linked: Linked[] = attachments.map((item) => {
    const href = blocked(item) || item.state === "not_stored" || item.state === "pending" ? null : hrefOf(item.aid);
    return { ...item, href, thumb: href && thumbOf ? thumbOf(item.aid) : href };
  });
  const out: ReactNode[] = [];
  const album = linked.filter((item) => item.kind === "album" && !blocked(item));
  const images = linked.filter((item) => item.kind === "image" && !blocked(item));
  const files = linked.filter((item) => item.kind === "file" && !blocked(item));
  images.forEach((item) => out.push(<ImageBlock file={item} key={item.aid} />));
  if (album.length === 1) out.push(<ImageBlock file={album[0]} key={album[0].aid} />);
  if (album.length > 1) out.push(<AlbumBlock files={album} key="album" />);
  if (files.length === 1 && isPdf(files[0])) out.push(<PdfBlock file={files[0]} key={files[0].aid} />);
  else if (files.length === 1)
    out.push(
      <div className="scax-attach" key={files[0].aid}>
        <FileCard href={files[0].href} mime={files[0].mime} name={files[0].name} size={files[0].size} />
      </div>,
    );
  else if (files.length > 1) out.push(<FileGroup files={files} key="files" />);
  linked.forEach((item) => {
    if (blocked(item)) out.push(<ExpiredCard file={item} key={item.aid} />);
    else if (item.kind === "video") out.push(<MediaChip key={item.aid} kind="video" />);
    else if (item.kind === "audio") out.push(<MediaChip key={item.aid} kind="voice" />);
    else if (item.kind === "sticker")
      out.push(
        <span className="scax-imsg-emoticon" key={item.aid}>
          {copy.emoticon}
        </span>,
      );
  });
  return <>{out}</>;
}

/** URL 미리보기(언퍼일) — 둥근 상자 · 사이트 · 굵은 제목 · 설명 2줄 · 도메인. 썸네일은 외부 그림이라 자리만 둔다. */
export function UnfurlCard({ unfurl }: { unfurl: Unfurl }) {
  const title = unfurl.href ? (
    <a className="scax-unfurl__title" href={unfurl.href} rel="noreferrer" target="_blank">
      {unfurl.title}
    </a>
  ) : (
    <span className="scax-unfurl__title">{unfurl.title}</span>
  );
  return (
    <div className="scax-unfurl scax-unfurl--text">
      <div className="scax-unfurl__text">
        {unfurl.site ? <span className="scax-unfurl__site">{unfurl.site}</span> : null}
        {unfurl.title ? title : null}
        {unfurl.desc ? <span className="scax-unfurl__desc">{unfurl.desc}</span> : null}
        {unfurl.domain ? <span className="scax-unfurl__domain">{unfurl.domain}</span> : null}
      </div>
    </div>
  );
}

/** 리액션 — 읽기 전용(추가 없음 · D-07). */
export function Reactions({ list }: { list: Reaction[] }) {
  if (!list.length) return null;
  return (
    <div aria-label={copy.reactions} className="scax-reacts">
      {list.map((reaction) => (
        <span className="scax-react" key={reaction.emoji}>
          <span className="scax-react__emoji">{reaction.emoji}</span>
          <span className="scax-react__count">{reaction.count}</span>
        </span>
      ))}
    </div>
  );
}
