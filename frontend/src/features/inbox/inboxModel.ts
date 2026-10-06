/**
 * 메시지함의 «원문 → 화면 모델» (WORK-011 FE-a · SPEC-008 §2.1·§5 프론트).
 *
 * 서버는 원문을 그대로 보관하고(D-28) 렌더는 프론트 책임이다. 그래서 여기서:
 * - 슬랙 메시지: **blocks(rich_text) 우선, 없으면 mrkdwn(`text`) 대체** → 시안의 블록 모양(`p`·`ul`·`ol`·`quote`·`pre`)
 * - 멘션 이름 풀기(`<@U…>` · rich_text `user`) — 이름표(`InboxPeople`)가 없으면 원문 `user_profile` 로
 * - 같은 날·같은 사람·5분 안 묶음 · 날짜 구분 · 시각 글자
 * - 메일 주소 한 줄(`이름 <주소>`) 풀기 · 답장 받는 사람 고르기
 *
 * React 를 모른다 — 그리는 일은 `InboxMessage.tsx` 가 한다. 시험은 옆자리 `inboxModel.test.ts`.
 */

import type { InboxAttachment, InboxPeople, InboxRoomMessage } from "../../lib/viewModels";

/* ===== 서식 조각 — 시안 `data.js` 의 세그먼트 모양 그대로 ===== */

export type Seg =
  | string
  | { b: string }
  | { i: string }
  | { s: string }
  | { code: string }
  | { mention: string }
  | { link: string; href: string }
  | { emoji: string };

export type ListItem = { segs: Seg[]; sub?: Seg[][] };

export type Block =
  | { p: Seg[] }
  | { ul: ListItem[] }
  | { ol: ListItem[] }
  | { quote: Seg[] }
  | { pre: string };

/* ===== 이모지 — rich_text 의 `unicode` 가 정본, 이름뿐이면 자주 쓰는 것만 푼다(없으면 `:이름:` 그대로) ===== */

const EMOJI: Record<string, string> = {
  "+1": "👍",
  thumbsup: "👍",
  "-1": "👎",
  heart: "❤️",
  smile: "😄",
  slightly_smiling_face: "🙂",
  joy: "😂",
  pray: "🙏",
  eyes: "👀",
  white_check_mark: "✅",
  heavy_check_mark: "✔️",
  tada: "🎉",
  fire: "🔥",
  ok_hand: "👌",
  clap: "👏",
  raised_hands: "🙌",
  "100": "💯",
  rocket: "🚀",
  thinking_face: "🤔",
  x: "❌",
  warning: "⚠️",
  bow: "🙇",
  sweat_smile: "😅",
  grinning: "😀",
};

export function emojiOf(name: string, unicode?: unknown): string {
  if (typeof unicode === "string" && unicode) {
    try {
      return String.fromCodePoint(...unicode.split("-").map((part) => Number.parseInt(part, 16)));
    } catch {
      /* 깨진 코드면 이름으로 */
    }
  }
  const base = name.replace(/::skin-tone-\d$/, "");
  return EMOJI[base] ?? `:${name}:`;
}

/* ===== 이름 풀기 ===== */

export type Person = { id: string; name: string; isBot: boolean };

/** 슬랙 메시지의 보낸 사람 — 이름표 → 원문 `user_profile` → `bot_profile`·`username` → id 순. */
export function slackAuthor(raw: Record<string, unknown>, author: string | null, people: InboxPeople): Person {
  const id = String(raw.user ?? author ?? raw.bot_id ?? raw.username ?? "");
  const known = people[id];
  const profile = raw.user_profile as { real_name?: string; display_name?: string } | undefined;
  const bot = raw.bot_profile as { name?: string } | undefined;
  const isBot = Boolean(known?.is_bot ?? (raw.bot_id || raw.subtype === "bot_message" || bot));
  const name = known?.name || profile?.real_name || profile?.display_name || bot?.name || (typeof raw.username === "string" ? raw.username : "") || id || "알 수 없음";
  return { id, name, isBot };
}

export function mentionName(userId: string, people: InboxPeople, fallback?: string): string {
  return people[userId]?.name || fallback || userId;
}

/* ===== 링크 스킴 — 여는 것은 http · https · mailto 뿐 (검수 F-1) =====
 * 슬랙 원문은 사람만 쓰지 않는다 — 봇·앱·웹훅이 정한 주소가 그대로 쌓인다(D-13). `javascript:`·`data:` 같은 주소를
 * 링크로 만들면 브라우저의 새 창이 우리 origin 을 물려받아 세션 쿠키로 `/api` 를 부를 수 있다. 그래서 허용 목록 밖은
 * **링크가 아니라 글자로** 그린다. 여는 자리(`openLink`)도 같은 검사를 한 번 더 한다. */

const SAFE_SCHEMES = new Set(["http:", "https:", "mailto:"]);

/** 열어도 되는 주소면 정규화한 주소를, 아니면 `null`. 상대 주소·스킴 없는 글자도 `null` 이다. */
export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url.trim());
    return SAFE_SCHEMES.has(parsed.protocol) ? parsed.href : null;
  } catch {
    return null;
  }
}

/** 링크 조각 — 안전하지 않으면 글자 조각으로 떨어진다. */
function linkSeg(label: string, href: string): Seg {
  const safe = safeHref(href);
  return safe ? { link: label || safe, href: safe } : label || href;
}

/* ===== 슬랙 mrkdwn → 조각 ===== */

const ENTITIES: Record<string, string> = { "&lt;": "<", "&gt;": ">", "&amp;": "&" };
const unescapeSlack = (text: string) => text.replace(/&(lt|gt|amp);/g, (match) => ENTITIES[match] ?? match);

/** 한 줄 안의 서식 — `<@U|이름>` · `<#C|채널>` · `<url|글>` · `<!here>` · `*굵게*` · `_기울임_` · `~취소~` · `` `코드` `` · `:이모지:` */
export function parseInline(text: string, people: InboxPeople): Seg[] {
  const out: Seg[] = [];
  const pattern = /<([@#!])?([^>|]+)(?:\|([^>]+))?>|`([^`]+)`|\*([^*\n]+)\*|(?<![\w])_([^_\n]+)_(?![\w])|~([^~\n]+)~|:([a-z0-9_+'-]+(?:::skin-tone-\d)?):/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const at = match.index ?? 0;
    if (at > last) out.push(unescapeSlack(text.slice(last, at)));
    const [, sigil, target, label, code, bold, italic, strike, emoji] = match;
    if (target !== undefined) {
      if (sigil === "@") out.push({ mention: label ? label.replace(/^@/, "") : mentionName(target, people) });
      else if (sigil === "#") out.push({ mention: label ? `#${label}` : `#${target}` });
      else if (sigil === "!") out.push({ mention: (label ?? target).replace(/^subteam\^\w+$/, "그룹") });
      else out.push(linkSeg(unescapeSlack(label ?? target), unescapeSlack(target)));
    } else if (code !== undefined) out.push({ code: unescapeSlack(code) });
    else if (bold !== undefined) out.push({ b: unescapeSlack(bold) });
    else if (italic !== undefined) out.push({ i: unescapeSlack(italic) });
    else if (strike !== undefined) out.push({ s: unescapeSlack(strike) });
    else if (emoji !== undefined) out.push({ emoji: emojiOf(emoji) });
    last = at + match[0].length;
  }
  if (last < text.length) out.push(unescapeSlack(text.slice(last)));
  return out;
}

const BULLET = /^\s*[•◦▪\-*]\s+/;
const ORDERED = /^\s*\d+[.)]\s+/;

/** mrkdwn 글 전체 → 블록. 코드 펜스 · 인용(`>`) · 글머리(`•`·`-`) · 번호 목록 · 문단. */
export function parseMrkdwn(text: string, people: InboxPeople): Block[] {
  const blocks: Block[] = [];
  const parts = text.split("```");
  parts.forEach((part, index) => {
    if (index % 2 === 1) {
      blocks.push({ pre: unescapeSlack(part.replace(/^\n|\n$/g, "")) });
      return;
    }
    let paragraph: string[] = [];
    let list: { kind: "ul" | "ol"; items: ListItem[] } | null = null;
    let quote: string[] = [];
    const flushParagraph = () => {
      if (paragraph.length) blocks.push({ p: parseInline(paragraph.join("\n"), people) });
      paragraph = [];
    };
    const flushList = () => {
      if (list) blocks.push(list.kind === "ul" ? { ul: list.items } : { ol: list.items });
      list = null;
    };
    const flushQuote = () => {
      if (quote.length) blocks.push({ quote: parseInline(quote.join("\n"), people) });
      quote = [];
    };
    for (const line of part.split("\n")) {
      if (/^&gt;\s?|^>\s?/.test(line)) {
        flushParagraph();
        flushList();
        quote.push(line.replace(/^(&gt;|>)\s?/, ""));
        continue;
      }
      flushQuote();
      const kind = BULLET.test(line) ? "ul" : ORDERED.test(line) ? "ol" : null;
      if (kind) {
        flushParagraph();
        if (!list || list.kind !== kind) {
          flushList();
          list = { kind, items: [] };
        }
        list.items.push({ segs: parseInline(line.replace(kind === "ul" ? BULLET : ORDERED, ""), people) });
        continue;
      }
      flushList();
      if (line.trim() === "") flushParagraph();
      else paragraph.push(line);
    }
    flushParagraph();
    flushList();
    flushQuote();
  });
  return blocks;
}

/* ===== 슬랙 blocks(rich_text) → 블록 ===== */

type RichElement = Record<string, unknown> & { type?: string };

function richSegs(elements: unknown, people: InboxPeople): Seg[] {
  if (!Array.isArray(elements)) return [];
  const out: Seg[] = [];
  for (const element of elements as RichElement[]) {
    const style = (element.style ?? {}) as { bold?: boolean; italic?: boolean; strike?: boolean; code?: boolean };
    switch (element.type) {
      case "text": {
        const text = String(element.text ?? "");
        if (style.code) out.push({ code: text });
        else if (style.bold) out.push({ b: text });
        else if (style.italic) out.push({ i: text });
        else if (style.strike) out.push({ s: text });
        else out.push(text);
        break;
      }
      case "link":
        out.push(linkSeg(String(element.text || ""), String(element.url ?? "")));
        break;
      case "user":
        out.push({ mention: mentionName(String(element.user_id ?? ""), people) });
        break;
      case "usergroup":
        out.push({ mention: "그룹" });
        break;
      case "channel":
        out.push({ mention: `#${people[String(element.channel_id)]?.name ?? String(element.channel_id ?? "")}` });
        break;
      case "broadcast":
        out.push({ mention: String(element.range ?? "here") });
        break;
      case "emoji":
        out.push({ emoji: emojiOf(String(element.name ?? ""), element.unicode) });
        break;
      default:
        if (typeof element.text === "string") out.push(element.text);
    }
  }
  return out;
}

function richBlock(block: RichElement, people: InboxPeople): Block[] {
  const out: Block[] = [];
  const elements = Array.isArray(block.elements) ? (block.elements as RichElement[]) : [];
  let pendingList: { kind: "ul" | "ol"; items: ListItem[] } | null = null;
  const flush = () => {
    if (pendingList) out.push(pendingList.kind === "ul" ? { ul: pendingList.items } : { ol: pendingList.items });
    pendingList = null;
  };
  for (const element of elements) {
    if (element.type === "rich_text_list") {
      const kind = element.style === "ordered" ? "ol" : "ul";
      const indent = Number(element.indent ?? 0);
      const items = (Array.isArray(element.elements) ? (element.elements as RichElement[]) : []).map((section) => richSegs(section.elements, people));
      if (indent > 0 && pendingList && pendingList.items.length) {
        /* 들여쓴 목록 = 바로 앞 항목의 하위 목록 (시안 `sub`) */
        const host = pendingList.items[pendingList.items.length - 1];
        host.sub = [...(host.sub ?? []), ...items];
        continue;
      }
      if (!pendingList || pendingList.kind !== kind) {
        flush();
        pendingList = { kind, items: [] };
      }
      pendingList.items.push(...items.map((segs) => ({ segs })));
      continue;
    }
    flush();
    if (element.type === "rich_text_section") {
      const segs = richSegs(element.elements, people);
      /* 줄바꿈 둘(빈 줄)이 문단을 가른다 */
      let current: Seg[] = [];
      for (const seg of segs) {
        if (typeof seg === "string" && seg.includes("\n\n")) {
          const pieces = seg.split(/\n{2,}/);
          pieces.forEach((piece, index) => {
            if (piece) current.push(piece);
            if (index < pieces.length - 1) {
              if (current.length) out.push({ p: current });
              current = [];
            }
          });
        } else current.push(seg);
      }
      if (current.some((seg) => (typeof seg === "string" ? seg.trim() !== "" : true))) out.push({ p: trimEdges(current) });
    } else if (element.type === "rich_text_preformatted") {
      out.push({ pre: richSegs(element.elements, people).map(segText).join("") });
    } else if (element.type === "rich_text_quote") {
      out.push({ quote: richSegs(element.elements, people) });
    }
  }
  flush();
  return out;
}

function trimEdges(segs: Seg[]): Seg[] {
  const copy = [...segs];
  if (typeof copy[0] === "string") copy[0] = copy[0].replace(/^\n+/, "");
  const end = copy.length - 1;
  if (typeof copy[end] === "string") copy[end] = (copy[end] as string).replace(/\n+$/, "");
  return copy.filter((seg) => seg !== "");
}

/** 슬랙 메시지 본문 — **blocks 우선**(rich_text · section mrkdwn), 그릴 블록이 없으면 `text` mrkdwn 으로 대체. */
export function slackBlocks(raw: Record<string, unknown>, people: InboxPeople): Block[] {
  const out: Block[] = [];
  if (Array.isArray(raw.blocks)) {
    for (const block of raw.blocks as RichElement[]) {
      if (block.type === "rich_text") out.push(...richBlock(block, people));
      else if (block.type === "section") {
        const text = block.text as { type?: string; text?: string } | undefined;
        if (text?.text) out.push(...(text.type === "mrkdwn" ? parseMrkdwn(text.text, people) : [{ p: [text.text] } as Block]));
        if (Array.isArray(block.fields)) {
          for (const field of block.fields as Array<{ type?: string; text?: string }>) {
            if (field.text) out.push(...parseMrkdwn(field.text, people));
          }
        }
      } else if (block.type === "context" && Array.isArray(block.elements)) {
        const text = (block.elements as Array<{ text?: string }>).map((item) => item.text ?? "").filter(Boolean).join(" · ");
        if (text) out.push(...parseMrkdwn(text, people));
      } else if (block.type === "header") {
        const text = (block.text as { text?: string } | undefined)?.text;
        if (text) out.push({ p: [{ b: text }] });
      }
    }
  }
  if (out.length) return out;
  const text = typeof raw.text === "string" ? raw.text : "";
  return text ? parseMrkdwn(text, people) : [];
}

/** 카톡 줄 — 글뿐이다(서식 없음). 빈 줄로 문단을 가른다. */
export function plainBlocks(text: unknown): Block[] {
  if (typeof text !== "string" || !text.trim()) return [];
  return text.split(/\n{2,}/).map((piece) => ({ p: [piece] }));
}

export function segText(seg: Seg): string {
  if (typeof seg === "string") return seg;
  if ("b" in seg) return seg.b;
  if ("i" in seg) return seg.i;
  if ("s" in seg) return seg.s;
  if ("code" in seg) return seg.code;
  if ("mention" in seg) return `@${seg.mention}`;
  if ("link" in seg) return seg.link;
  return seg.emoji;
}

/* ===== URL 미리보기 · 리액션 ===== */

export type Unfurl = { site: string; title: string; desc: string; domain: string; href: string | null; hasThumb: boolean };

/** 슬랙 원문의 `attachments`(언퍼일) — 저장된 정보로 바로 그린다(§2.2). 썸네일 그림은 외부 주소라 싣지 않는다. */
export function slackUnfurls(raw: Record<string, unknown>): Unfurl[] {
  if (!Array.isArray(raw.attachments)) return [];
  return (raw.attachments as Array<Record<string, unknown>>)
    .filter((item) => item.title || item.text || item.service_name)
    .map((item) => {
      const href = safeHref(String(item.title_link ?? item.original_url ?? item.from_url ?? ""));
      let domain = "";
      try {
        domain = href ? new URL(href).hostname : "";
      } catch {
        domain = "";
      }
      return {
        site: String(item.service_name ?? domain ?? ""),
        title: String(item.title ?? ""),
        desc: String(item.text ?? item.fallback ?? ""),
        domain,
        href,
        hasThumb: Boolean(item.thumb_url || item.image_url),
      };
    });
}

export type Reaction = { emoji: string; count: number };

export function slackReactions(raw: Record<string, unknown>): Reaction[] {
  if (!Array.isArray(raw.reactions)) return [];
  return (raw.reactions as Array<{ name?: string; count?: number }>)
    .filter((item) => item.name)
    .map((item) => ({ emoji: emojiOf(String(item.name)), count: Number(item.count ?? 1) }));
}

/* ===== 시각 ===== */

const pad = (value: number) => String(value).padStart(2, "0");
const WEEK = ["일", "월", "화", "수", "목", "금", "토"];

export function dayKeyOf(at: string): string {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function clockOf(at: string): string {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return "";
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** 날짜 알약 — 오늘 · 어제 · 「10월 4일 (토)」 */
export function dayLabelOf(at: string, now: Date = new Date()): string {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return "";
  const today = dayKeyOf(now.toISOString());
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const key = dayKeyOf(at);
  if (key === today) return "오늘";
  if (key === dayKeyOf(yesterday.toISOString())) return "어제";
  const year = date.getFullYear() !== now.getFullYear() ? `${date.getFullYear()}년 ` : "";
  return `${year}${date.getMonth() + 1}월 ${date.getDate()}일 (${WEEK[date.getDay()]})`;
}

/** 카드의 짧은 시각 — 오늘이면 「09:04」, 어제면 「어제」, 그 밖은 「10-03」. */
export function shortWhen(at: string, now: Date = new Date()): string {
  const label = dayLabelOf(at, now);
  if (label === "오늘") return clockOf(at);
  if (label === "어제") return "어제";
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return "";
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** 메일 머리의 날짜 — 「2026년 10월 6일 (화) 오전 9:04」 */
export function longWhen(at: string): string {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return at;
  const hours = date.getHours();
  const half = hours < 12 ? "오전" : "오후";
  const h12 = hours % 12 === 0 ? 12 : hours % 12;
  return `${date.getFullYear()}년 ${date.getMonth() + 1}월 ${date.getDate()}일 (${WEEK[date.getDay()]}) ${half} ${h12}:${pad(date.getMinutes())}`;
}

/* ===== 대화 줄 묶기 ===== */

export type GroupLine = { userId: string; at: string; status?: string | null };

/** 같은 날 · 같은 사람 · 5분 안이면 묶는다(아바타·이름 없이 왼쪽에 시각만). 보내는 중·실패 줄은 묶지 않는다. */
export function isGrouped(prev: GroupLine | undefined, line: GroupLine): boolean {
  if (!prev || prev.status || line.status) return false;
  if (prev.userId !== line.userId || dayKeyOf(prev.at) !== dayKeyOf(line.at)) return false;
  const gap = new Date(line.at).getTime() - new Date(prev.at).getTime();
  return gap >= 0 && gap <= 5 * 60 * 1000;
}

/** 방 본문에 서는 줄 — 스레드 답글은 빼고(오른쪽 패널에서 본다) 부모·일반·「채널에도 보냄」만. */
export function isTopLevel(message: InboxRoomMessage): boolean {
  if (!message.thread_key || message.thread_key === message.key) return true;
  return message.raw.subtype === "thread_broadcast";
}

/* ===== 첨부 ===== */

export function fmtSize(bytes: number | null | undefined): string {
  if (bytes == null) return "";
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function extOf(name: string): string {
  const match = /\.([a-z0-9]+)$/i.exec(name);
  return match ? match[1].toLowerCase() : "file";
}

/** 파일 표식의 종류 — 시안 FILE_TYPE 의 열쇠(pdf·md·xlsx·docx·zip·png·jpg)로 모은다. */
export function fileTypeOf(name: string, mime?: string | null): string {
  const ext = extOf(name);
  if (ext === "jpeg") return "jpg";
  if (ext === "xls" || ext === "csv") return "xlsx";
  if (ext === "doc" || ext === "hwp") return "docx";
  if (ext !== "file") return ext;
  if (mime === "application/pdf") return "pdf";
  if (mime?.startsWith("image/")) return "png";
  return "file";
}

export function isPdf(attachment: InboxAttachment): boolean {
  return attachment.mime === "application/pdf" || extOf(attachment.name) === "pdf";
}

export function isImage(attachment: InboxAttachment): boolean {
  return attachment.kind === "image" || attachment.kind === "album" || Boolean(attachment.mime?.startsWith("image/"));
}

/** 카드·미리보기에 서는 첨부 한 줄 — 글이 없을 때 무엇이 왔는지 말한다. */
export function attachmentSummary(list: InboxAttachment[]): string {
  if (!list.length) return "";
  const album = list.filter((item) => item.kind === "album").length;
  if (album > 1) return `사진 ${album}장`;
  const first = list[0];
  if (first.kind === "image" || first.kind === "album") return "사진";
  if (first.kind === "video") return "동영상";
  if (first.kind === "audio") return "음성 메시지";
  if (first.kind === "sticker") return "(이모티콘)";
  return "파일";
}

/* ===== 메일 주소 ===== */

export type Address = { name: string; addr: string };

/** `이름 <주소>` · `"이름" <주소>` · `주소` 한 줄을 푼다. */
export function parseAddress(line: string): Address {
  const match = /^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$/.exec(line);
  if (match) {
    const addr = match[2].trim();
    return { name: match[1].trim() || addr.split("@")[0], addr };
  }
  const addr = line.trim();
  return { name: addr.split("@")[0], addr };
}

export function formatAddress(address: Address): string {
  return address.name && address.name !== address.addr.split("@")[0] ? `${address.name} <${address.addr}>` : address.addr;
}

export function peopleLine(lines: string[]): string {
  return lines.map(parseAddress).map((item) => `${item.name} <${item.addr}>`).join(", ");
}

/** 답장 받는 사람 — 답장 = 회신 주소(없으면 보낸 사람) · 전체 답장 = 그 + 받는 사람 + 참조(나는 뺀다). */
export function replyRecipients(
  mail: { sender: string | null; reply_to: string[]; to: string[]; cc: string[]; account: string },
  mode: "reply" | "all",
): { to: Address[]; cc: Address[] } {
  const me = parseAddress(mail.account).addr.toLowerCase();
  const first = (mail.reply_to.length ? mail.reply_to : mail.sender ? [mail.sender] : []).map(parseAddress);
  if (mode === "reply") return { to: first, cc: [] };
  const seen = new Set(first.map((item) => item.addr.toLowerCase()));
  seen.add(me);
  const pick = (lines: string[]) =>
    lines.map(parseAddress).filter((item) => {
      const key = item.addr.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  const to = [...first, ...pick(mail.to)];
  return { to, cc: pick(mail.cc) };
}

/** 메일 답장 첨부 한도 — **합계**(인코딩 전) 25MB(W-11). 슬랙 보내기는 파일 하나당 50MB. */
export const MAIL_LIMIT_BYTES = 25 * 1024 * 1024;
export const SLACK_FILE_LIMIT_BYTES = 50 * 1024 * 1024;

/** 방 종류 글자 — 「슬랙 · 채널」·「카카오톡 · 단체방」의 뒷부분. */
export function roomKindLabel(kind: "slack" | "kakao", roomType: string): string {
  if (kind === "kakao") return roomType === "direct" ? "1:1" : "단체방";
  if (roomType === "channel") return "채널";
  if (roomType === "private") return "비공개 채널";
  if (roomType === "dm") return "DM";
  return "그룹 DM";
}
