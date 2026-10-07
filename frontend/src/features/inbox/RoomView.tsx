import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Button, IconButton } from "../../ds/Button";
import { Empty } from "../../ds/Empty";
import { Icon } from "../../ds/icons/Icon";
import { getInboxRoomMessages, inboxRoomAttachmentDownloadUrl, inboxRoomAttachmentUrl, replyToInboxRoom } from "../../lib/api";
import { createIdempotencyKey } from "../../lib/idempotency";
import { inboxScreen as copy } from "../../lib/labels";
import type { InboxAttachment, InboxPeople, InboxRoomCard, InboxRoomMessage, InboxRoomPage } from "../../lib/viewModels";
import { AttachmentList, FileCard, Reactions, UnfurlCard } from "./InboxAttachments";
import {
  clockOf,
  dayKeyOf,
  dayLabelOf,
  isGrouped,
  isTopLevel,
  plainBlocks,
  roomKindLabel,
  safeHref,
  slackAuthor,
  slackBlocks,
  slackReactions,
  slackUnfurls,
  type Block,
  type ListItem,
  type Reaction,
  type Seg,
  type Unfurl,
} from "./inboxModel";
import { openLink, type InboxEventHub } from "./inboxStream";
import { RoomComposer } from "./RoomComposer";

/**
 * 슬랙·카톡 대화방 본문 (SPEC-008 §2.1 · DC-1·5·6) — 진짜 슬랙 대화방처럼.
 *
 * 날짜 구분 · 아바타·이름·시각 · 같은 사람 5분 안 묶음 · 서식(blocks 우선 / mrkdwn 대체) · URL 미리보기 · 첨부 ·
 * 읽기 전용 리액션 · 봇 `앱` 표시 · 「새 메시지 N개」. 「답글 N개」를 누르면 **오른쪽 스레드 패널**(3열)이 열린다.
 * 카톡은 같은 모양에 **입력창·스레드·외부 열기가 없다**(조회 전용, D-17).
 */

const ME = "__me__";

type SendStatus = "sending" | "failed" | "sent";

/** 내가 방금 보낸 줄 — 서버 사본이 다시 읽힐 때까지 화면에 선다. */
type LocalLine = { idempotencyKey: string; localId: string | null; text: string; files: File[]; status: SendStatus; at: string; threadTs: string | null };

type ChatLine = {
  id: string;
  key: string;
  at: string;
  userId: string;
  name: string;
  isBot: boolean;
  blocks: Block[];
  unfurls: Unfurl[];
  reactions: Reaction[];
  attachments: InboxAttachment[];
  localFiles: File[];
  thread: { count: number; users: string[]; last: string | null } | null;
  status: SendStatus | null;
  local: LocalLine | null;
  /** 이 메시지로 확정된 업무 수(SPEC-008 §4.8 ④) — 1 이상이면 「업무 만듦」. 로컬 줄은 0. */
  madeTaskCount: number;
};

/**
 * 메시지 호버 막대의 행동(SPEC-008 §2.9 ① · OQ-908) — 슬랙: 스레드에 답글 · AX 업무 생성 · AX 요약 / 카톡: AX 둘.
 * 스레드 패널 안(답글·부모)에서는 「스레드에 답글」 이 빠진다(OQ-813). 로컬 줄(보내는 중·실패)에는 막대가 없다.
 */
type MessageActions = { onThread?: () => void; onAxTask?: () => void; onAxSummary?: () => void };

function toLine(message: InboxRoomMessage, kind: "slack" | "kakao", people: InboxPeople): ChatLine {
  const raw = message.raw ?? {};
  if (kind === "kakao") {
    const author = message.author ?? (typeof raw.author === "string" ? raw.author : "");
    return {
      id: message.id,
      key: message.key,
      at: message.at,
      userId: author,
      name: people[author]?.name || author || "알 수 없음",
      isBot: false,
      blocks: plainBlocks(raw.text),
      unfurls: [],
      reactions: [],
      attachments: message.attachments,
      localFiles: [],
      thread: null,
      status: null,
      local: null,
      madeTaskCount: message.made_task_count ?? 0,
    };
  }
  const person = slackAuthor(raw, message.author, people);
  const replyCount = Number(raw.reply_count ?? 0);
  return {
    id: message.id,
    key: message.key,
    at: message.at,
    userId: person.id,
    name: person.name,
    isBot: person.isBot,
    blocks: slackBlocks(raw, people),
    unfurls: slackUnfurls(raw),
    reactions: slackReactions(raw),
    attachments: message.attachments,
    localFiles: [],
    thread:
      replyCount > 0 && (!message.thread_key || message.thread_key === message.key)
        ? {
            count: replyCount,
            users: Array.isArray(raw.reply_users) ? (raw.reply_users as string[]) : [],
            last: typeof raw.latest_reply === "string" ? new Date(Number(raw.latest_reply) * 1000).toISOString() : null,
          }
        : null,
    status: null,
    local: null,
    madeTaskCount: message.made_task_count ?? 0,
  };
}

function localLine(local: LocalLine, meName: string): ChatLine {
  return {
    id: `local-${local.idempotencyKey}`,
    key: local.idempotencyKey,
    at: local.at,
    userId: ME,
    name: meName,
    isBot: false,
    blocks: local.text ? plainBlocks(local.text) : [],
    unfurls: [],
    reactions: [],
    attachments: [],
    localFiles: local.files,
    thread: null,
    status: local.status === "sent" ? null : local.status,
    local,
    madeTaskCount: 0,
  };
}

/**
 * 호버 막대 — 행 오른쪽 위에 겹쳐 뜨는 **아이콘만** 있는 작은 막대(1차 호버 · 본문을 밀지 않는다).
 * 아이콘에 호버(또는 포커스)하면 그 위에 툴팁(2차 — `data-tip`). 행에 키보드로 들어와도 막대가 선다(`:focus-within`).
 */
function MessageBar({ actions }: { actions: MessageActions }) {
  const tool = (tip: string, icon: "message" | "sparkle" | "document", onClick: () => void) => (
    <span className="scax-imsg__tool" data-tip={tip} key={tip}>
      <IconButton label={tip} name={icon} onClick={onClick} size={16} />
    </span>
  );
  return (
    <div aria-label={copy.messageBar} className="scax-imsg__bar" role="toolbar">
      {actions.onThread ? tool(copy.threadReply, "message", actions.onThread) : null}
      {actions.onAxTask ? tool(copy.axTask, "sparkle", actions.onAxTask) : null}
      {actions.onAxSummary ? tool(copy.axSummary, "document", actions.onAxSummary) : null}
    </div>
  );
}

/* ===== 서식 ===== */

function Segs({ segs }: { segs: Seg[] }) {
  return (
    <>
      {segs.map((seg, index) => {
        if (typeof seg === "string") return <Fragment key={index}>{seg}</Fragment>;
        if ("b" in seg) return <strong key={index}>{seg.b}</strong>;
        if ("i" in seg) return <em key={index}>{seg.i}</em>;
        if ("s" in seg) return <s key={index}>{seg.s}</s>;
        if ("code" in seg)
          return (
            <code className="scax-imsg-code" key={index}>
              {seg.code}
            </code>
          );
        if ("mention" in seg)
          return (
            <span className="scax-imsg-mention" key={index}>
              @{seg.mention}
            </span>
          );
        if ("link" in seg)
          return (
            <a
              className="scax-imsg-link"
              href={seg.href}
              key={index}
              onClick={(event) => {
                event.preventDefault();
                void openLink(seg.href);
              }}
              rel="noreferrer"
              target="_blank"
            >
              {seg.link}
            </a>
          );
        return (
          <span className="scax-imsg-emoji" key={index}>
            {seg.emoji}
          </span>
        );
      })}
    </>
  );
}

function Items({ items }: { items: ListItem[] }) {
  return (
    <>
      {items.map((item, index) => (
        <li key={index}>
          <Segs segs={item.segs} />
          {item.sub?.length ? (
            <ul className="scax-imsg-list scax-imsg-list--sub">
              {item.sub.map((sub, subIndex) => (
                <li key={subIndex}>
                  <Segs segs={sub} />
                </li>
              ))}
            </ul>
          ) : null}
        </li>
      ))}
    </>
  );
}

export function Blocks({ blocks }: { blocks: Block[] }) {
  return (
    <>
      {blocks.map((block, index) => {
        if ("p" in block)
          return (
            <p className="scax-imsg-p" key={index}>
              <Segs segs={block.p} />
            </p>
          );
        if ("ol" in block)
          return (
            <ol className="scax-imsg-list" key={index}>
              <Items items={block.ol} />
            </ol>
          );
        if ("ul" in block)
          return (
            <ul className="scax-imsg-list" key={index}>
              <Items items={block.ul} />
            </ul>
          );
        if ("quote" in block)
          return (
            <blockquote className="scax-imsg-quote" key={index}>
              <Segs segs={block.quote} />
            </blockquote>
          );
        return (
          <pre className="scax-imsg-pre" key={index}>
            {block.pre}
          </pre>
        );
      })}
    </>
  );
}

/* ===== 메시지 ===== */

const TONES = ["a", "b", "c", "d", "e"] as const;
function toneOf(userId: string, isBot: boolean): string {
  if (isBot) return "bot";
  if (userId === ME) return "a";
  let hash = 0;
  for (const char of userId) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return TONES[hash % TONES.length];
}

function Avatar({ name, userId, isBot, size = "md" }: { name: string; userId: string; isBot: boolean; size?: "md" | "xs" }) {
  return (
    <span aria-hidden className={`scax-imsg-avatar scax-imsg-avatar--${toneOf(userId, isBot)} scax-imsg-avatar--${size}`}>
      {name.trim().slice(0, 1) || "?"}
    </span>
  );
}

function SendState({ line, onResend }: { line: ChatLine; onResend: (line: LocalLine) => void }) {
  if (line.status === "sending") return <span className="scax-imsg-state">{copy.sending}</span>;
  if (line.status === "failed" && line.local) {
    const local = line.local;
    return (
      <span className="scax-imsg-state scax-imsg-state--failed" role="alert">
        <Icon name="circle-exclamation" size={16} />
        {copy.sendFailed}
        <button className="scax-imsg-state__retry" onClick={() => onResend(local)} type="button">
          {copy.resend}
        </button>
      </span>
    );
  }
  return null;
}

function ThreadLine({ line, open, onOpen, nameOf }: { line: ChatLine; open: boolean; onOpen: () => void; nameOf: (id: string) => string }) {
  const thread = line.thread;
  if (!thread) return null;
  return (
    <div className="scax-thread">
      <button aria-expanded={open} className={`scax-thread__line${open ? " scax-thread__line--open" : ""}`} onClick={onOpen} type="button">
        <span className="scax-thread__faces">
          {thread.users.slice(0, 3).map((id) => (
            <Avatar isBot={false} key={id} name={nameOf(id)} size="xs" userId={id} />
          ))}
        </span>
        <span className="scax-thread__count">{copy.replies(thread.count)}</span>
        {thread.last ? <span className="scax-thread__last">{copy.lastReply(`${dayLabelOf(thread.last)} ${clockOf(thread.last)}`)}</span> : null}
        <Icon name="chevron-right" size={16} />
      </button>
    </div>
  );
}

function Message({
  line,
  grouped,
  hrefOf,
  downloadOf,
  thumbOf,
  onResend,
  threadOpen,
  onOpenThread,
  inPanel,
  nameOf,
  actions,
  focused,
}: {
  line: ChatLine;
  grouped: boolean;
  /** 원본 주소 — 웹의 원본 보기(새 탭 · `inline`). */
  hrefOf: (aid: string) => string;
  /** 받기 주소(`?download=1`) — 받기 단추 · 「모두 다운로드」 · 앱의 원본 보기(SPEC-008 §2.2). `hrefOf` 와 같은 길로 내린다. */
  downloadOf: (aid: string) => string;
  /** 대화 안 이미지 미리보기 — 썸네일(`?variant=thumb`). */
  thumbOf: (aid: string) => string;
  onResend: (line: LocalLine) => void;
  threadOpen?: boolean;
  onOpenThread?: () => void;
  inPanel?: boolean;
  nameOf: (id: string) => string;
  /** 호버 막대 행동 — 없으면 막대가 서지 않는다. 로컬 줄에는 부르는 쪽이 넘기지 않는다. */
  actions?: MessageActions | null;
  /** 출처 링크로 들어와 짚은 메시지 — 잠깐 강조한다(OQ-817). */
  focused?: boolean;
}) {
  const classes = ["scax-imsg"];
  const bar = actions && !line.local && (actions.onThread || actions.onAxTask || actions.onAxSummary) ? actions : null;
  if (focused) classes.push("scax-imsg--focus");
  if (grouped) classes.push("scax-imsg--grouped");
  if (line.status) classes.push(`scax-imsg--${line.status}`);
  if (threadOpen && !inPanel) classes.push("scax-imsg--active");
  return (
    <div className={classes.join(" ")} data-message-id={line.local ? undefined : line.id} data-message-key={line.key} tabIndex={bar ? 0 : undefined}>
      {bar ? <MessageBar actions={bar} /> : null}
      <div className="scax-imsg__gutter">
        {grouped ? <span className="scax-imsg__at-side">{clockOf(line.at)}</span> : <Avatar isBot={line.isBot} name={line.name} userId={line.userId} />}
      </div>
      <div className="scax-imsg__main">
        {grouped ? null : (
          <div className="scax-imsg__head">
            <span className="scax-imsg__name">{line.name}</span>
            {line.isBot ? <span className="scax-imsg__app">{copy.app}</span> : null}
            <span className="scax-imsg__at">{clockOf(line.at)}</span>
            {line.madeTaskCount > 0 ? <span className="scax-imsg__made">{copy.madeTask}</span> : null}
          </div>
        )}
        {/* 묶인 줄은 머리가 없다 — 표지를 본문 위 한 줄로 */}
        {grouped && line.madeTaskCount > 0 ? <span className="scax-imsg__made scax-imsg__made--line">{copy.madeTask}</span> : null}
        {line.blocks.length ? (
          <div className="scax-imsg__body">
            <Blocks blocks={line.blocks} />
          </div>
        ) : null}
        {line.unfurls.map((unfurl, index) => (
          <UnfurlCard key={index} unfurl={unfurl} />
        ))}
        <AttachmentList attachments={line.attachments} downloadOf={downloadOf} hrefOf={hrefOf} thumbOf={thumbOf} />
        {line.localFiles.length ? (
          <div className="scax-attach">
            <div className="scax-fcard-row">
              {line.localFiles.map((file, index) => (
                <FileCard key={`${file.name}-${index}`} mime={file.type} name={file.name} size={file.size} />
              ))}
            </div>
          </div>
        ) : null}
        <Reactions list={line.reactions} />
        {line.status ? <SendState line={line} onResend={onResend} /> : null}
        {!inPanel && onOpenThread ? <ThreadLine line={line} nameOf={nameOf} onOpen={onOpenThread} open={Boolean(threadOpen)} /> : null}
      </div>
    </div>
  );
}

/** 날짜 알약 + 묶음 계산을 한 번에 — 대화 열과 스레드 패널이 같이 쓴다. */
function Log({ lines, render, dividers = true }: { lines: ChatLine[]; render: (line: ChatLine, grouped: boolean) => ReactNode; dividers?: boolean }) {
  const rows: ReactNode[] = [];
  lines.forEach((line, index) => {
    const prev = lines[index - 1];
    if (dividers && (!prev || dayKeyOf(prev.at) !== dayKeyOf(line.at))) {
      rows.push(
        <div className="scax-day-divider" key={`d-${dayKeyOf(line.at)}-${index}`}>
          <span className="scax-day-divider__pill">{dayLabelOf(line.at)}</span>
        </div>,
      );
    }
    const grouped = isGrouped(prev ? { userId: prev.userId, at: prev.at, status: prev.status } : undefined, { userId: line.userId, at: line.at, status: line.status });
    rows.push(<Fragment key={line.id}>{render(line, grouped)}</Fragment>);
  });
  return <>{rows}</>;
}

/* ===== 보내기 — 슬랙만 ===== */

function useSender(roomId: string, hub: InboxEventHub) {
  const [locals, setLocals] = useState<LocalLine[]>([]);
  const patch = useCallback((key: string, change: Partial<LocalLine>) => {
    setLocals((current) => current.map((line) => (line.idempotencyKey === key ? { ...line, ...change } : line)));
  }, []);
  const deliver = useCallback(
    async (line: LocalLine) => {
      try {
        const accepted = await replyToInboxRoom(roomId, { text: line.text, threadTs: line.threadTs, files: line.files }, line.idempotencyKey);
        patch(line.idempotencyKey, { localId: accepted.local_id });
      } catch {
        patch(line.idempotencyKey, { status: "failed" });
      }
    },
    [patch, roomId],
  );
  const send = useCallback(
    (text: string, files: File[], threadTs: string | null) => {
      const line: LocalLine = { idempotencyKey: createIdempotencyKey(), localId: null, text, files, status: "sending", at: new Date().toISOString(), threadTs };
      setLocals((current) => [...current, line]);
      void deliver(line);
    },
    [deliver],
  );
  /* 「다시 보내기」 — **같은 `Idempotency-Key`** 로. 서버는 실패한 것만 다시 보낸다(AC-12). */
  const resend = useCallback(
    (line: LocalLine) => {
      patch(line.idempotencyKey, { status: "sending" });
      void deliver({ ...line, status: "sending" });
    },
    [deliver, patch],
  );
  /* 결과는 사용자 사건으로 온다(202 뒤 성공/실패 · N-9). */
  useEffect(
    () =>
      hub.subscribe((event) => {
        if (event.type !== "inbox.reply_result" || !event.data?.local_id) return;
        const status = event.data.status === "sent" ? "sent" : event.data.status === "failed" ? "failed" : null;
        if (!status) return;
        setLocals((current) => current.map((line) => (line.localId === event.data?.local_id ? { ...line, status } : line)));
      }),
    [hub],
  );
  /* 서버 사본을 다시 읽으면 보냄이 끝난 줄은 걷는다 — 같은 글이 두 번 서지 않게. */
  const settle = useCallback(() => setLocals((current) => current.filter((line) => line.status !== "sent")), []);
  return { locals, send, resend, settle };
}

/* ===== 스레드 패널 ===== */

function ThreadPanel({
  roomId,
  title,
  parent,
  people,
  meName,
  hub,
  hrefOf,
  downloadOf,
  thumbOf,
  onClose,
  nameOf,
  onAsk,
}: {
  roomId: string;
  title: string;
  parent: ChatLine;
  people: InboxPeople;
  meName: string;
  hub: InboxEventHub;
  /** 원본 주소 — 웹의 원본 보기(새 탭 · `inline`). */
  hrefOf: (aid: string) => string;
  /** 받기 주소(`?download=1`) — 받기 단추 · 「모두 다운로드」 · 앱의 원본 보기(SPEC-008 §2.2). `hrefOf` 와 같은 길로 내린다. */
  downloadOf: (aid: string) => string;
  /** 대화 안 이미지 미리보기 — 썸네일(`?variant=thumb`). */
  thumbOf: (aid: string) => string;
  onClose: () => void;
  nameOf: (id: string) => string;
  /** AX 업무 생성 · AX 요약 — 패널 안 메시지는 스레드 전체가 맥락이다(서버가 조합 · §4.8 ②). */
  onAsk?: (kind: "task" | "summary", messageId: string) => void;
}) {
  const [replies, setReplies] = useState<InboxRoomMessage[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const sender = useSender(roomId, hub);
  const { settle } = sender;
  const load = useCallback(async () => {
    try {
      const page = await getInboxRoomMessages(roomId, { threadTs: parent.key });
      setReplies(page.messages.filter((message) => message.key !== parent.key));
      setState("ready");
      settle();
    } catch {
      setState("error");
    }
  }, [parent.key, roomId, settle]);
  useEffect(() => {
    setState("loading");
    void load();
  }, [load]);
  useEffect(
    () =>
      hub.subscribe((event) => {
        /* 새 답글 · 「업무 만듦」 이 바뀜(`inbox.message_updated`) — 그 방이면 다시 읽는다 */
        if ((event.type === "inbox.message_arrived" || event.type === "inbox.message_updated") && event.room_id === roomId) void load();
      }),
    [hub, load, roomId],
  );
  /* 패널 안에서는 「스레드에 답글」 이 빠진다 — 이미 그 스레드다(OQ-813) */
  const panelActions = (line: ChatLine): MessageActions | null =>
    onAsk ? { onAxTask: () => onAsk("task", line.id), onAxSummary: () => onAsk("summary", line.id) } : null;
  const lines = [...replies.map((message) => toLine(message, "slack", people)), ...sender.locals.map((local) => localLine(local, meName))];
  return (
    <aside aria-label={copy.threadAria(title)} className="scax-thread-panel">
      <header className="scax-thread-panel__head">
        <h3 className="scax-thread-panel__title">
          {copy.threadTitle} <span className="scax-thread-panel__room">· {title}</span>
        </h3>
        <IconButton label={copy.threadClose} name="close" onClick={onClose} />
      </header>
      <div className="scax-thread-panel__log">
        <Message actions={panelActions(parent)} grouped={false} downloadOf={downloadOf} hrefOf={hrefOf} thumbOf={thumbOf} inPanel line={parent} nameOf={nameOf} onResend={sender.resend} />
        <div className="scax-thread-panel__count">
          <span>{copy.replies(Math.max(replies.length, parent.thread?.count ?? 0))}</span>
        </div>
        {state === "error" ? (
          <Empty actionLabel={copy.retry} description={copy.retryDesc} onAction={() => void load()} title={copy.threadError} variant="error" />
        ) : (
          <Log dividers={false} lines={lines} render={(line, grouped) => <Message actions={panelActions(line)} grouped={grouped} downloadOf={downloadOf} hrefOf={hrefOf} thumbOf={thumbOf} inPanel line={line} nameOf={nameOf} onResend={sender.resend} />} />
        )}
      </div>
      <div className="scax-thread-panel__compose">
        <RoomComposer key={parent.key} onSend={(text, files) => sender.send(text, files, parent.key)} placeholder={copy.composeThread} />
      </div>
    </aside>
  );
}

/* ===== 대화방 ===== */

export function RoomView({
  card,
  meName,
  hub,
  onRead,
  onAsk,
  focusMessageId = null,
  onFocusHandled,
}: {
  card: InboxRoomCard;
  meName: string;
  hub: InboxEventHub;
  /** 그 방을 이 key 까지 읽었다 — 부모가 서버에 알리고 카드 숫자를 고친다. */
  onRead: (roomId: string, upTo: string) => void;
  /** 호버 막대의 AX 업무 생성 · AX 요약 — 부모가 서랍을 열고 그 메시지를 참고 자료로 보낸다(SPEC-008 §2.9 ③). */
  onAsk?: (kind: "task" | "summary", messageId: string) => void;
  /** 출처 링크로 들어왔다 — 이 메시지까지 스크롤하고 잠깐 강조한다(OQ-817). */
  focusMessageId?: string | null;
  /** 짚기를 마쳤다(찾았든 못 찾았든) — `found` 가 거짓이면 부모가 「메시지를 찾을 수 없습니다」 를 낸다. */
  onFocusHandled?: (found: boolean) => void;
}) {
  const roomId = card.room_id;
  const kakao = card.kind === "kakao";
  const [page, setPage] = useState<InboxRoomPage | null>(null);
  const [messages, setMessages] = useState<InboxRoomMessage[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [older, setOlder] = useState(false);
  const [threadKey, setThreadKey] = useState<string | null>(null);
  const [newCount, setNewCount] = useState(0);
  const log = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  /** 지금 화면에 선 메시지 — 새로 읽은 페이지와 잇는 기준(렌더마다 갱신). */
  const held = useRef<InboxRoomMessage[]>([]);
  held.current = messages;
  const sender = useSender(roomId, hub);
  const { settle } = sender;

  const people = useMemo<InboxPeople>(() => page?.users ?? {}, [page]);
  const nameOf = useCallback((id: string) => people[id]?.name ?? id, [people]);
  const hrefOf = useCallback((aid: string) => inboxRoomAttachmentUrl(roomId, aid), [roomId]);
  const downloadOf = useCallback((aid: string) => inboxRoomAttachmentDownloadUrl(roomId, aid), [roomId]);
  const thumbOf = useCallback((aid: string) => inboxRoomAttachmentUrl(roomId, aid, "thumb"), [roomId]);

  const load = useCallback(
    async (mode: "first" | "refresh") => {
      try {
        const next = await getInboxRoomMessages(roomId);
        setPage(next);
        if (mode === "first") setMessages(next.messages);
        else {
          /* 새로 읽은 최신 페이지를 이미 가진 과거 쪽과 잇는다 — 위로 읽어 둔 것을 잃지 않는다 */
          const current = held.current;
          const added = next.messages.filter((message) => !current.some((item) => item.id === message.id)).length;
          if (added && !stick.current) setNewCount((count) => count + added);
          const known = new Set(next.messages.map((message) => message.id));
          /* 새로 읽은 쪽이 이긴다 — 같은 메시지의 바뀐 값(「업무 만듦」 수 등)이 화면에 선다 */
          setMessages([...current.filter((message) => !known.has(message.id)), ...next.messages]);
        }
        if (mode === "first") setCursor(next.next_cursor);
        setState("ready");
        settle();
        const last = next.messages[next.messages.length - 1];
        if (last && (stick.current || mode === "first")) onRead(roomId, last.key);
      } catch {
        if (mode === "first") setState("error");
      }
    },
    [onRead, roomId, settle],
  );

  useEffect(() => {
    setState("loading");
    setMessages([]);
    setThreadKey(null);
    setNewCount(0);
    stick.current = true;
    void load("first");
  }, [load]);

  useEffect(
    () =>
      hub.subscribe((event) => {
        /* 새 메시지 · 「업무 만듦」 이 바뀜(`inbox.message_updated` · SPEC-008 §4.4) — 그 방이면 다시 읽어 새로고침 없이 표지를 세운다 */
        if ((event.type === "inbox.message_arrived" || event.type === "inbox.message_updated") && event.room_id === roomId) void load("refresh");
      }),
    [hub, load, roomId],
  );

  const loadOlder = async () => {
    if (!cursor || older) return;
    setOlder(true);
    const box = log.current;
    const before = box ? box.scrollHeight - box.scrollTop : 0;
    try {
      const next = await getInboxRoomMessages(roomId, { cursor });
      setMessages((current) => [...next.messages.filter((message) => !current.some((item) => item.id === message.id)), ...current]);
      setCursor(next.next_cursor);
      window.requestAnimationFrame(() => {
        if (box) box.scrollTop = box.scrollHeight - before;
      });
    } catch {
      /* 위로 읽기 실패는 단추가 그대로 남아 다시 누를 수 있다 */
    } finally {
      setOlder(false);
    }
  };

  const lines = useMemo(
    () => [...messages.filter(isTopLevel).map((message) => toLine(message, kakao ? "kakao" : "slack", people)), ...sender.locals.map((local) => localLine(local, meName))],
    [kakao, meName, messages, people, sender.locals],
  );

  /* 아래에 붙어 있으면 새 줄이 와도 아래를 지킨다. 위로 올려 읽는 중이면 「새 메시지 N개」 알약을 세운다. */
  useLayoutEffect(() => {
    const box = log.current;
    if (box && stick.current) box.scrollTop = box.scrollHeight;
  }, [lines.length, state]);

  const toBottom = () => {
    const box = log.current;
    if (box) box.scrollTop = box.scrollHeight;
    stick.current = true;
    setNewCount(0);
    const last = messages[messages.length - 1];
    if (last) onRead(roomId, last.key);
  };

  const header = page?.room;
  const permalink = safeHref(header?.permalink);
  const title = header?.name ?? card.title;
  const roomType = header?.room_type ?? card.room_type;
  const members = header?.member_count ?? card.member_count;
  const parent = threadKey ? lines.find((line) => line.key === threadKey) ?? null : null;

  /*
   * 호버 막대(SPEC-008 §2.9 ①) — 슬랙: 스레드에 답글 · AX 업무 · AX 요약 / 카톡: AX 둘.
   * 「스레드에 답글」 은 **답글이 0개여도** 그 메시지로 패널을 연다(D-33 — 입구만 더함, 패널·조회·답장 경로엔 조건이 없다).
   */
  const rowActions = (line: ChatLine): MessageActions | null => {
    if (line.local) return null;
    return {
      onThread: kakao ? undefined : () => setThreadKey(line.key),
      onAxTask: onAsk ? () => onAsk("task", line.id) : undefined,
      onAxSummary: onAsk ? () => onAsk("summary", line.id) : undefined,
    };
  };

  /*
   * 출처 링크로 들어온 메시지 짚기(OQ-817) — 화면의 최상위 줄에 있으면 거기까지 스크롤 + 잠깐 강조.
   * 스레드 답글이면 그 스레드 패널을 열고 부모를 짚는다. 처음 페이지에 없으면 위로 몇 쪽 더 읽어 본다(최대 3쪽).
   */
  const [highlight, setHighlight] = useState<string | null>(null);
  const focusTries = useRef(0);
  useEffect(() => {
    if (!focusMessageId || state !== "ready") return;
    const target = messages.find((message) => message.id === focusMessageId);
    if (!target) {
      if (cursor && focusTries.current < 3 && !older) {
        focusTries.current += 1;
        void loadOlder();
        return;
      }
      onFocusHandled?.(false);
      return;
    }
    const topLevel = isTopLevel(target);
    const anchorId = topLevel ? target.id : messages.find((message) => message.key === target.thread_key)?.id ?? target.id;
    if (!topLevel && target.thread_key && !kakao) setThreadKey(target.thread_key);
    stick.current = false;
    window.requestAnimationFrame(() => {
      const escaped = typeof CSS !== "undefined" && typeof CSS.escape === "function" ? CSS.escape(anchorId) : anchorId.replace(/"/g, '\\"');
      const row = log.current?.querySelector<HTMLElement>(`[data-message-id="${escaped}"]`);
      row?.scrollIntoView?.({ block: "center" });
    });
    setHighlight(anchorId);
    const timer = window.setTimeout(() => setHighlight(null), 2500);
    onFocusHandled?.(true);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 짚기는 «그 메시지가 처음 보일 때» 한 번이다
  }, [focusMessageId, state, messages.length]);
  const placeholder = roomType === "channel" || roomType === "private" ? copy.composeChannel(title) : copy.composePerson(title);

  return (
    <div className={`scax-inbox-main scax-inbox-main--room${parent ? " scax-inbox-main--thread" : ""}`}>
      <div className="scax-room-col">
        <header className="scax-room-head">
          <div className="scax-room-head__lead">
            <h2 className="scax-room-head__title">{title}</h2>
            <div className="scax-room-head__meta">
              <span>
                {kakao ? copy.kakaoKind : copy.slackKind} · {roomKindLabel(kakao ? "kakao" : "slack", roomType)}
              </span>
              {members != null ? (
                <>
                  <span className="scax-inbox-card__meta-sep" />
                  <span>{copy.members(members)}</span>
                </>
              ) : null}
              {kakao ? (
                <>
                  <span className="scax-inbox-card__meta-sep" />
                  <span>{copy.kakaoReadOnly}</span>
                </>
              ) : null}
            </div>
          </div>
          {!kakao && permalink ? (
            <a
              className="scax-room-head__open"
              href={permalink}
              onClick={(event) => {
                event.preventDefault();
                void openLink(permalink);
              }}
              rel="noreferrer"
              target="_blank"
            >
              <Icon name="link" size={16} />
              {copy.openInSlack}
            </a>
          ) : null}
        </header>
        <div
          className="scax-room-log"
          onScroll={(event) => {
            const box = event.currentTarget;
            stick.current = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
            if (stick.current && newCount) toBottom();
          }}
          ref={log}
        >
          {state === "loading" ? (
            <div className="scax-room-log__inner">
              <div className="scax-inbox-skel" role="status" aria-busy="true">
                <span className="sr-only">{copy.loadingList}</span>
                <span className="scax-skeleton scax-skeleton--title scax-skeleton--w-60" />
                {[0, 1, 2, 3].map((index) => (
                  <span className={`scax-skeleton scax-skeleton--text scax-skeleton--w-${index % 2 ? "80" : "full"}`} key={index} />
                ))}
              </div>
            </div>
          ) : state === "error" ? (
            <div className="scax-room-log__inner">
              <Empty actionLabel={copy.retry} description={copy.retryDesc} onAction={() => void load("first")} title={copy.bodyError} variant="error" />
            </div>
          ) : (
            <div className="scax-room-log__inner">
              {cursor ? (
                <div className="scax-room-log__older">
                  <Button disabled={older} label={copy.loadOlder} onClick={() => void loadOlder()} size="sm" variant="text" />
                </div>
              ) : null}
              <Log
                lines={lines}
                render={(line, grouped) => (
                  <Message
                    grouped={grouped}
                    downloadOf={downloadOf} hrefOf={hrefOf} thumbOf={thumbOf}
                    line={line}
                    nameOf={nameOf}
                    onOpenThread={kakao ? undefined : () => setThreadKey(line.key)}
                    onResend={sender.resend}
                    threadOpen={threadKey === line.key}
                    actions={rowActions(line)}
                    focused={highlight === line.id}
                  />
                )}
              />
            </div>
          )}
          {newCount ? (
            <div className="scax-room-log__new" role="status">
              <button className="scax-room-log__new-pill" onClick={toBottom} style={{ pointerEvents: "auto" }} type="button">
                <Icon name="arrow-down" size={16} />
                {copy.newMessages(newCount)}
              </button>
            </div>
          ) : null}
        </div>
        {/* 카톡은 조회 전용 — 입력창이 없다 */}
        {kakao ? null : (
          <div className="scax-room-compose">
            <RoomComposer
              key={roomId}
              onSend={(text, files) => {
                stick.current = true;
                sender.send(text, files, null);
              }}
              placeholder={placeholder}
            />
          </div>
        )}
      </div>
      {parent && !kakao ? (
        <ThreadPanel
          downloadOf={downloadOf} hrefOf={hrefOf} thumbOf={thumbOf}
          hub={hub}
          key={parent.key}
          meName={meName}
          nameOf={nameOf}
          onAsk={onAsk}
          onClose={() => setThreadKey(null)}
          parent={parent}
          people={people}
          roomId={roomId}
          title={title}
        />
      ) : null}
    </div>
  );
}
