import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "../../ds/Button";
import { DropZone } from "../../ds/DropZone";
import { Empty } from "../../ds/Empty";
import { FileList } from "../../ds/FileList";
import { Icon } from "../../ds/icons/Icon";
import { getInboxMail, inboxMailAttachmentDownloadUrl, inboxMailAttachmentUrl, replyToInboxMail } from "../../lib/api";
import { createIdempotencyKey } from "../../lib/idempotency";
import { inboxScreen as copy } from "../../lib/labels";
import type { InboxMail, InboxMailCard, InboxSentReply } from "../../lib/viewModels";
import { DownloadLink, FileCard, FileMark, Thumb } from "./InboxAttachments";
import {
  extOf,
  fileTypeOf,
  fmtSize,
  formatAddress,
  isImage,
  longWhen,
  MAIL_LIMIT_BYTES,
  parseAddress,
  peopleLine,
  replyRecipients,
  shortWhen,
  type Address,
} from "./inboxModel";
import type { InboxEventHub } from "./inboxStream";
import { MailFrame } from "./MailFrame";

/**
 * 메일 본문 (SPEC-008 §2.1·§2.8 · DC-1·3·5) — 폭 전체 · 머리 표 · HTML 원문(샌드박스 iframe) · 첨부 · 답장.
 *
 * 답장은 원문 아래에 펼치는 작성 칸이다(모달 아님). `202` 뒤 결과는 사용자 사건으로 오고(N-9), 보내지면 칸을 닫고
 * 원문 아래 「보낸 답장」(= 우리가 보낸 기록, D-47)으로 선다. 전달·새 메일 쓰기는 없다.
 */

type Staged = { key: string; file: File };
let stageSeq = 0;
const stage = (files: File[]): Staged[] =>
  files.map((file) => {
    stageSeq += 1;
    return { key: `m${stageSeq}`, file };
  });

/* ===== 받는 사람 · 참조 칩 ===== */

function PeopleField({ label, list, onChange, disabled }: { label: string; list: Address[]; onChange: (next: Address[]) => void; disabled: boolean }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const value = draft.trim().replace(/,$/, "");
    if (!value) return;
    const address = parseAddress(value);
    if (!list.some((item) => item.addr.toLowerCase() === address.addr.toLowerCase())) onChange([...list, address]);
    setDraft("");
  };
  return (
    <div className="scax-reply__row">
      <span className="scax-reply__label">{label}</span>
      <div className="scax-reply__chips">
        {list.map((person) => (
          <span className="scax-reply__chip" key={person.addr} title={person.addr}>
            {person.name}
            <span className="scax-reply__chip-addr">{person.addr}</span>
            <button
              aria-label={copy.removeAddress(person.name)}
              className="scax-reply__chip-x"
              disabled={disabled}
              onClick={() => onChange(list.filter((item) => item.addr !== person.addr))}
              type="button"
            >
              <Icon name="close" size={14} />
            </button>
          </span>
        ))}
        <input
          aria-label={copy.addAddress(label)}
          className="scax-reply__chip-input"
          disabled={disabled}
          onBlur={add}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === ",") {
              event.preventDefault();
              add();
            }
          }}
          placeholder={list.length ? "" : copy.addressPlaceholder}
          value={draft}
        />
      </div>
    </div>
  );
}

/* ===== 답장 작성 칸 ===== */

type Phase = "draft" | "sending" | "failed";

function ReplyBox({
  mail,
  mode,
  phase,
  onCancel,
  onSend,
}: {
  mail: InboxMail;
  mode: "reply" | "all";
  phase: Phase;
  onCancel: () => void;
  onSend: (payload: { to: Address[]; cc: Address[]; body: string; files: File[] }) => void;
}) {
  const initial = replyRecipients(mail, mode);
  const [to, setTo] = useState(initial.to);
  const [cc, setCc] = useState(initial.cc);
  const [body, setBody] = useState("");
  const [files, setFiles] = useState<Staged[]>([]);
  const [quoteOpen, setQuoteOpen] = useState(false);
  const busy = phase === "sending";
  const total = files.reduce((sum, item) => sum + item.file.size, 0);
  const overTotal = total > MAIL_LIMIT_BYTES;
  const canSend = to.length > 0 && (body.trim() !== "" || files.length > 0) && !overTotal && !busy;
  const sender = parseAddress(mail.sender ?? "");
  return (
    <section aria-label={mode === "all" ? copy.replyBox.all : copy.replyBox.reply} className={`scax-reply${busy ? " scax-reply--busy" : ""}`}>
      <header className="scax-reply__head">
        <span className="scax-reply__kind">{mode === "all" ? copy.replyAll : copy.reply}</span>
        <span className="scax-reply__from">{copy.fromAccount(mail.account)}</span>
      </header>
      <PeopleField disabled={busy} label={copy.to} list={to} onChange={setTo} />
      <PeopleField disabled={busy} label={copy.cc} list={cc} onChange={setCc} />
      <div className="scax-reply__row">
        <span className="scax-reply__label">{copy.subject}</span>
        <span className="scax-reply__subject">Re: {mail.subject ?? ""}</span>
      </div>
      <textarea
        aria-label={copy.replyBody}
        className="scax-reply__body"
        disabled={busy}
        onChange={(event) => setBody(event.target.value)}
        placeholder={copy.replyPlaceholder}
        value={body}
      />
      <div className="scax-reply__quote">
        <button aria-expanded={quoteOpen} className="scax-mail__quote-toggle" onClick={() => setQuoteOpen((value) => !value)} type="button">
          {quoteOpen ? copy.quoteClose : copy.quoteOpen}
        </button>
        {quoteOpen ? (
          <div className="scax-mail-html scax-mail-html--quoted">
            <p className="quote-head">
              {longWhen(mail.at)}, {sender.name} &lt;{sender.addr}&gt; 작성:
            </p>
            {/* 인용도 원문이다 — 앱 문서에 그대로 넣지 않고 같은 샌드박스 iframe 에 담는다(F-3). */}
            <MailFrame html={mail.safe_html} messageId={mail.message_id} />
          </div>
        ) : null}
      </div>
      <DropZone disabled={busy} drop={copy.mailDrop} hint={copy.mailHint} onFiles={(list) => setFiles((current) => current.concat(stage(list)))} pickLabel={copy.pickFile}>
        {files.length ? (
          <FileList
            label={copy.outgoing}
            rows={files.map((item) => ({
              key: item.key,
              name: item.file.name,
              size: fmtSize(item.file.size),
              icon: /^(png|jpe?g|gif|webp)$/.test(extOf(item.file.name)) ? "blank" : "document",
              reason: item.file.size > MAIL_LIMIT_BYTES ? copy.overMailFile : null,
              onRemove: busy ? undefined : () => setFiles((current) => current.filter((row) => row.key !== item.key)),
              removeLabel: copy.removeAddress(item.file.name),
            }))}
          />
        ) : null}
      </DropZone>
      {overTotal && !files.some((item) => item.file.size > MAIL_LIMIT_BYTES) ? (
        <p className="scax-set-error" role="alert">
          {copy.overMailTotal}
        </p>
      ) : null}
      {phase === "failed" ? (
        <Empty
          actionLabel={copy.resend}
          className="scax-status-note--inline"
          description={copy.replyFailedDesc}
          onAction={() => onSend({ to, cc, body, files: files.map((item) => item.file) })}
          title={copy.replyFailedTitle}
          variant="error"
        />
      ) : null}
      <footer className="scax-reply__foot">
        <Button disabled={busy} label={copy.cancel} onClick={onCancel} tone="neutral" variant="outlined" />
        <Button
          disabled={!canSend}
          iconBefore="send"
          label={busy ? copy.sending : copy.send}
          onClick={() => onSend({ to, cc, body, files: files.map((item) => item.file) })}
          tone="primary"
          variant="solid"
        />
      </footer>
    </section>
  );
}

/* ===== 보낸 답장 — 원문 아래 한 덩어리 ===== */

function SentReply({ sent }: { sent: InboxSentReply }) {
  const names = (lines?: string[]) => (lines ?? []).map((line) => parseAddress(line).name).join(", ");
  const when = sent.sent_at ? shortWhen(sent.sent_at) : "";
  const files = sent.payload.files ?? [];
  return (
    <section aria-label={copy.sentBadge} className="scax-sent">
      <header className="scax-sent__head">
        <span className="scax-sent__badge">
          <Icon name="send" size={16} />
          {copy.sentBadge}
        </span>
        <span className="scax-sent__meta">{copy.sentMeta(names(sent.payload.to), names(sent.payload.cc), when)}</span>
      </header>
      {sent.payload.body ? <p className="scax-sent__body">{sent.payload.body}</p> : null}
      {files.length ? (
        <div className="scax-fcard-row">
          {files.map((file, index) => (
            <FileCard key={`${file.name}-${index}`} mime={file.mime} name={file.name} size={file.size} />
          ))}
        </div>
      ) : null}
    </section>
  );
}

/* ===== 메일 본문 ===== */

export function MailView({
  card,
  hub,
  onRead,
  onAsk,
}: {
  card: InboxMailCard;
  hub: InboxEventHub;
  onRead: (messageId: string) => void;
  /** 머리의 [AX 업무 생성] [AX 요약](SPEC-008 §2.9 ②) — 부모가 서랍을 열고 이 메일을 참고 자료로 보낸다. 없으면 단추가 서지 않는다. */
  onAsk?: (kind: "task" | "summary", messageId: string) => void;
}) {
  const messageId = card.message_id;
  const [mail, setMail] = useState<InboxMail | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [compose, setCompose] = useState<{ mode: "reply" | "all"; phase: Phase } | null>(null);
  /** 이 작성 칸의 멱등 키와 서버가 준 `local_id` — 「다시 보내기」는 같은 키로 간다. */
  const attempt = useRef<{ key: string; localId: string | null } | null>(null);
  const poll = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await getInboxMail(messageId);
      setMail(next);
      setState("ready");
      return next;
    } catch {
      setState("error");
      return null;
    }
  }, [messageId]);

  useEffect(() => {
    setState("loading");
    setCompose(null);
    attempt.current = null;
    void load().then((next) => {
      if (next?.unread || card.unread) onRead(messageId);
    });
    // 카드를 열 때 한 번만 읽음을 건다 — 같은 메일을 다시 읽을 때(답장 뒤)는 걸지 않는다.
  }, [load, messageId]);

  useEffect(() => () => {
    if (poll.current !== null) window.clearInterval(poll.current);
  }, []);

  /* 답장 결과 — 사건이 정본이고, 사건이 안 오는 경우를 위해 기록(`sent_replies`)도 몇 번 다시 읽는다. */
  const settle = useCallback(
    (status: string) => {
      if (poll.current !== null) window.clearInterval(poll.current);
      poll.current = null;
      if (status === "sent") {
        setCompose(null);
        attempt.current = null;
        void load();
      } else if (status === "failed") setCompose((current) => (current ? { ...current, phase: "failed" } : current));
    },
    [load],
  );

  useEffect(
    () =>
      hub.subscribe((event) => {
        if (event.type === "inbox.reply_result" && event.data?.local_id && event.data.local_id === attempt.current?.localId && event.data.status) settle(event.data.status);
        /* 이 메일로 업무가 확정됐다(`inbox.message_updated` · 메일은 `room_id` 가 없다) — 다시 읽어 「업무 만듦」 을 새로고침 없이 세운다 */
        if (event.type === "inbox.message_updated" && !event.room_id && event.message_id === messageId) void load();
      }),
    [hub, load, messageId, settle],
  );

  const send = async (payload: { to: Address[]; cc: Address[]; body: string; files: File[] }) => {
    if (!compose) return;
    if (!attempt.current) attempt.current = { key: createIdempotencyKey(), localId: null };
    const current = attempt.current;
    setCompose({ ...compose, phase: "sending" });
    try {
      const accepted = await replyToInboxMail(
        messageId,
        { replyAll: compose.mode === "all", body: payload.body, to: payload.to.map(formatAddress), cc: payload.cc.map(formatAddress), files: payload.files },
        current.key,
      );
      current.localId = accepted.local_id;
      let tries = 0;
      if (poll.current !== null) window.clearInterval(poll.current);
      poll.current = window.setInterval(() => {
        tries += 1;
        void getInboxMail(messageId)
          .then((next) => {
            const record = next.sent_replies.find((item) => item.local_id === current.localId);
            if (record && record.status !== "sending") settle(record.status);
          })
          .catch(() => undefined);
        if (tries >= 10 && poll.current !== null) {
          window.clearInterval(poll.current);
          poll.current = null;
        }
      }, 3000);
    } catch {
      setCompose((value) => (value ? { ...value, phase: "failed" } : value));
    }
  };

  if (state === "loading") {
    return (
      <div className="scax-inbox-main">
        <div aria-busy="true" className="scax-inbox-skel" role="status">
          <span className="sr-only">{copy.loadingList}</span>
          <span className="scax-skeleton scax-skeleton--title scax-skeleton--w-60" />
          {[0, 1, 2, 3, 4].map((index) => (
            <span className={`scax-skeleton scax-skeleton--text scax-skeleton--w-${index % 2 ? "80" : "full"}`} key={index} />
          ))}
        </div>
      </div>
    );
  }
  if (state === "error" || !mail) {
    return (
      <div className="scax-inbox-main scax-inbox-main--empty">
        <Empty actionLabel={copy.retry} description={copy.retryDesc} onAction={() => void load()} title={copy.bodyError} variant="error" />
      </div>
    );
  }

  const sender = parseAddress(mail.sender ?? "");
  const images = mail.attachments.filter(isImage);
  const files = mail.attachments.filter((item) => !isImage(item));
  const sent = mail.sent_replies.filter((item) => item.status === "sent");

  return (
    <div className="scax-inbox-main">
      <article className="scax-mail">
        <div className="scax-mail__top">
          <h2 className="scax-mail__title">
            {mail.subject ?? ""}
            {(mail.made_task_count ?? 0) > 0 ? <span className="scax-imsg__made scax-mail__made">{copy.madeTask}</span> : null}
          </h2>
          <div className="scax-mail__actions">
            {/* AX 단추 둘이 답장 앞에 선다(SPEC-008 §2.8 · D-36) — 글자 단추라 툴팁이 없다 */}
            {onAsk ? (
              <>
                <Button label={copy.axTask} onClick={() => onAsk("task", mail.message_id)} size="sm" tone="neutral" variant="outlined" />
                <Button label={copy.axSummary} onClick={() => onAsk("summary", mail.message_id)} size="sm" tone="neutral" variant="outlined" />
              </>
            ) : null}
            <Button disabled={Boolean(compose)} label={copy.reply} onClick={() => setCompose({ mode: "reply", phase: "draft" })} size="sm" tone="neutral" variant="outlined" />
            <Button disabled={Boolean(compose)} label={copy.replyAll} onClick={() => setCompose({ mode: "all", phase: "draft" })} size="sm" tone="neutral" variant="outlined" />
          </div>
        </div>
        <dl className="scax-mail__head">
          <dt>{copy.from}</dt>
          <dd>
            <strong>{sender.name}</strong> <span className="scax-mail__addr">&lt;{sender.addr}&gt;</span>
          </dd>
          <dt>{copy.to}</dt>
          <dd>{peopleLine(mail.to)}</dd>
          {mail.cc.length ? (
            <>
              <dt>{copy.cc}</dt>
              <dd>{peopleLine(mail.cc)}</dd>
            </>
          ) : null}
          <dt>{copy.date}</dt>
          <dd>{longWhen(mail.at)}</dd>
          <dt>{copy.account}</dt>
          <dd>{mail.account}</dd>
        </dl>
        {/* 원문은 HTML 메일 그대로 담는다 — 다만 앱 문서가 아니라 샌드박스 iframe 에(F-3) */}
        <MailFrame html={mail.safe_html} messageId={mail.message_id} />
        {mail.attachments.length ? (
          <section className="scax-mail__files">
            <h3 className="scax-mail__files-title">{copy.attachCount(mail.attachments.length)}</h3>
            {images.length ? (
              <div className="scax-mail__images">
                {images.map((image) => {
                  /* 미리보기(`src` · 웹의 원본 보기)와 받기(`?download=1`)는 주소가 다르다(SPEC-008 §2.2) —
                     받기 주소는 받기 단추와 **앱의 원본 보기**(`Thumb` 의 `download`)가 쓴다 */
                  const src = inboxMailAttachmentUrl(mail.message_id, image.aid);
                  const download = inboxMailAttachmentDownloadUrl(mail.message_id, image.aid);
                  return (
                    <figure className="scax-mail__image" key={image.aid}>
                      <Thumb alt={image.name} download={download} size="md" src={src} />
                      <figcaption className="scax-fcard">
                        <FileMark size="sm" type={fileTypeOf(image.name, image.mime)} />
                        <span className="scax-fcard__text">
                          <span className="scax-fcard__name">{image.name}</span>
                          <span className="scax-fcard__type">{fmtSize(image.size)}</span>
                        </span>
                        {/* 받기 단추 — 파일 카드와 같은 부품(앱에서는 받는 동안 도는 원 · E-2) */}
                        <DownloadLink href={download} name={image.name} />
                      </figcaption>
                    </figure>
                  );
                })}
              </div>
            ) : null}
            {files.length ? (
              <div className="scax-fcard-row">
                {files.map((file) => (
                  <FileCard href={inboxMailAttachmentDownloadUrl(mail.message_id, file.aid)} key={file.aid} mime={file.mime} name={file.name} size={file.size} />
                ))}
              </div>
            ) : null}
          </section>
        ) : null}
        {sent.map((item) => (
          <SentReply key={item.local_id} sent={item} />
        ))}
        {compose ? (
          <ReplyBox
            key={compose.mode}
            mail={mail}
            mode={compose.mode}
            onCancel={() => {
              setCompose(null);
              attempt.current = null;
            }}
            onSend={(payload) => void send(payload)}
            phase={compose.phase}
          />
        ) : null}
      </article>
    </div>
  );
}
