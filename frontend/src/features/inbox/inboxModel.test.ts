import { describe, expect, it } from "vitest";

import type { InboxRoomMessage } from "../../lib/viewModels";
import {
  attachmentSummary,
  dayLabelOf,
  emojiOf,
  fileTypeOf,
  isGrouped,
  isTopLevel,
  parseAddress,
  parseInline,
  parseMrkdwn,
  safeHref,
  replyRecipients,
  shortWhen,
  slackAuthor,
  slackBlocks,
  slackReactions,
  slackUnfurls,
} from "./inboxModel";

const people = { U1: { name: "한서윤" }, U2: { name: "오지훈" }, B1: { name: "배포 알리미", is_bot: true } };

describe("슬랙 렌더 — blocks 우선 · mrkdwn 대체 (SPEC-008 §5 프론트)", () => {
  it("mrkdwn: 굵게 · 코드 · 링크 · 멘션 이름 풀기 · 이모지 · 엔티티", () => {
    expect(parseInline("*배포* 완료 `v1.2` <https://a.example|문서> <@U1> :tada: &lt;끝&gt;", people)).toEqual([
      { b: "배포" },
      " 완료 ",
      { code: "v1.2" },
      " ",
      { link: "문서", href: "https://a.example/" },
      " ",
      { mention: "한서윤" },
      " ",
      { emoji: "🎉" },
      " <끝>",
    ]);
  });

  it("mrkdwn: 글머리 · 번호 목록 · 인용 · 코드 펜스를 블록으로 가른다", () => {
    const blocks = parseMrkdwn("안내\n• 하나\n• 둘\n1. 첫째\n> 인용\n```\ncode\n```", people);
    expect(blocks).toEqual([
      { p: ["안내"] },
      { ul: [{ segs: ["하나"] }, { segs: ["둘"] }] },
      { ol: [{ segs: ["첫째"] }] },
      { quote: ["인용"] },
      { pre: "code" },
    ]);
  });

  it("rich_text blocks 가 있으면 text 보다 앞선다 — 들여쓴 목록은 앞 항목의 하위 목록", () => {
    const raw = {
      text: "무시되는 대체 글",
      blocks: [
        {
          type: "rich_text",
          elements: [
            { type: "rich_text_section", elements: [{ type: "text", text: "공지 ", style: { bold: true } }, { type: "user", user_id: "U2" }] },
            { type: "rich_text_list", style: "ordered", elements: [{ type: "rich_text_section", elements: [{ type: "text", text: "계정 발급" }] }] },
            { type: "rich_text_list", style: "bullet", indent: 1, elements: [{ type: "rich_text_section", elements: [{ type: "text", text: "중복 3건" }] }] },
            { type: "rich_text_section", elements: [{ type: "emoji", name: "thumbsup", unicode: "1f44d" }] },
          ],
        },
      ],
    };
    expect(slackBlocks(raw, people)).toEqual([
      { p: [{ b: "공지 " }, { mention: "오지훈" }] },
      { ol: [{ segs: ["계정 발급"], sub: [["중복 3건"]] }] },
      { p: [{ emoji: "👍" }] },
    ]);
  });

  it("blocks 가 비면 text(mrkdwn)로 대체한다", () => {
    expect(slackBlocks({ text: "그냥 *글*" }, people)).toEqual([{ p: ["그냥 ", { b: "글" }] }]);
  });

  it("보낸 사람 — 이름표 → user_profile → bot_profile, 봇은 표시", () => {
    expect(slackAuthor({ user: "U1" }, "U1", people)).toEqual({ id: "U1", name: "한서윤", isBot: false });
    expect(slackAuthor({ user: "U9", user_profile: { real_name: "문다은" } }, "U9", {})).toMatchObject({ name: "문다은", isBot: false });
    expect(slackAuthor({ bot_id: "B7", bot_profile: { name: "질문 도우미" } }, "B7", {})).toMatchObject({ name: "질문 도우미", isBot: true });
  });

  it("언퍼일 · 리액션(읽기 전용)", () => {
    const raw = {
      attachments: [{ service_name: "노을랩", title: "공지", title_link: "https://noeul.example/a", text: "설명", thumb_url: "https://x/y.png" }],
      reactions: [{ name: "+1", count: 3 }, { name: "custom_party", count: 1 }],
    };
    expect(slackUnfurls(raw)).toEqual([{ site: "노을랩", title: "공지", desc: "설명", domain: "noeul.example", href: "https://noeul.example/a", hasThumb: true }]);
    expect(slackReactions(raw)).toEqual([
      { emoji: "👍", count: 3 },
      { emoji: ":custom_party:", count: 1 },
    ]);
    expect(emojiOf("fire")).toBe("🔥");
  });
});

describe("링크 스킴 — http · https · mailto 만 (검수 F-1)", () => {
  it("javascript: · data: · 상대 주소는 링크가 아니라 글자로", () => {
    expect(safeHref("https://a.example/x")).toBe("https://a.example/x");
    expect(safeHref("mailto:a@b.example")).toBe("mailto:a@b.example");
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref(" JavaScript:alert(1)")).toBeNull();
    expect(safeHref("data:text/html,<script>")).toBeNull();
    expect(safeHref("/api/x")).toBeNull();
    expect(parseInline("<javascript:alert(1)|보기> <https://ok.example|좋음>", {})).toEqual(["보기", " ", { link: "좋음", href: "https://ok.example/" }]);
    const raw = {
      blocks: [{ type: "rich_text", elements: [{ type: "rich_text_section", elements: [{ type: "link", url: "javascript:alert(1)", text: "누르기" }] }] }],
      attachments: [{ title: "미끼", title_link: "javascript:alert(1)" }],
    };
    expect(slackBlocks(raw, {})).toEqual([{ p: ["누르기"] }]);
    expect(slackUnfurls(raw)[0].href).toBeNull();
  });
});

describe("대화 묶음 · 스레드", () => {
  it("같은 날 · 같은 사람 · 5분 안이면 묶고, 보내는 중·실패 줄은 묶지 않는다", () => {
    const a = { userId: "U1", at: "2026-10-06T00:00:00Z" };
    expect(isGrouped(a, { userId: "U1", at: "2026-10-06T00:05:00Z" })).toBe(true);
    expect(isGrouped(a, { userId: "U1", at: "2026-10-06T00:05:01Z" })).toBe(false);
    expect(isGrouped(a, { userId: "U2", at: "2026-10-06T00:01:00Z" })).toBe(false);
    expect(isGrouped(a, { userId: "U1", at: "2026-10-06T00:01:00Z", status: "sending" })).toBe(false);
    expect(isGrouped(undefined, a)).toBe(false);
  });

  it("방 본문에는 스레드 답글이 서지 않는다(채널에도 보낸 답글은 선다)", () => {
    const base: InboxRoomMessage = { id: "1", key: "100.1", at: "", author: "U1", thread_key: null, raw: {}, attachments: [] };
    expect(isTopLevel(base)).toBe(true);
    expect(isTopLevel({ ...base, thread_key: "100.1" })).toBe(true);
    expect(isTopLevel({ ...base, key: "101.1", thread_key: "100.1" })).toBe(false);
    expect(isTopLevel({ ...base, key: "101.1", thread_key: "100.1", raw: { subtype: "thread_broadcast" } })).toBe(true);
  });
});

describe("메일 주소 · 답장 받는 사람", () => {
  const mail = {
    sender: "서지안 <jian@noeul.example>",
    reply_to: [],
    to: ["유하람 <haram@company.example>", "문다은 <daeun@company.example>"],
    cc: ["한서윤 <seoyoon@company.example>", "HARAM@company.example"],
    account: "haram@company.example",
  };

  it("「이름 <주소>」 와 맨 주소를 푼다", () => {
    expect(parseAddress('"서지안" <jian@noeul.example>')).toEqual({ name: "서지안", addr: "jian@noeul.example" });
    expect(parseAddress("ops@noeul.example")).toEqual({ name: "ops", addr: "ops@noeul.example" });
  });

  it("답장 = 보낸 사람만 · 전체 답장 = 보낸 사람 + 받는 사람 + 참조, 나(받은 계정)는 뺀다", () => {
    expect(replyRecipients(mail, "reply")).toEqual({ to: [{ name: "서지안", addr: "jian@noeul.example" }], cc: [] });
    const all = replyRecipients(mail, "all");
    expect(all.to.map((item) => item.addr)).toEqual(["jian@noeul.example", "daeun@company.example"]);
    expect(all.cc.map((item) => item.addr)).toEqual(["seoyoon@company.example"]);
  });

  it("회신 주소(Reply-To)가 있으면 그쪽으로 답한다", () => {
    expect(replyRecipients({ ...mail, reply_to: ["ops@noeul.example"] }, "reply").to).toEqual([{ name: "ops", addr: "ops@noeul.example" }]);
  });
});

describe("첨부 · 시각", () => {
  it("파일 표식 종류는 시안 열쇠로 모은다", () => {
    expect(fileTypeOf("일정표.XLSX")).toBe("xlsx");
    expect(fileTypeOf("사진.jpeg")).toBe("jpg");
    expect(fileTypeOf("noext", "application/pdf")).toBe("pdf");
  });

  it("글이 없는 카톡 줄은 첨부 종류로 말한다", () => {
    const item = { aid: "a", name: "", size: null, mime: null, state: "stored" };
    expect(attachmentSummary([{ ...item, kind: "album" }, { ...item, aid: "b", kind: "album" }])).toBe("사진 2장");
    expect(attachmentSummary([{ ...item, kind: "audio" }])).toBe("음성 메시지");
    expect(attachmentSummary([{ ...item, kind: "sticker" }])).toBe("(이모티콘)");
  });

  it("날짜 알약 — 오늘 · 어제 · 그 밖은 월일(요일)", () => {
    const now = new Date(2026, 9, 6, 12, 0);
    expect(dayLabelOf(new Date(2026, 9, 6, 9, 4).toISOString(), now)).toBe("오늘");
    expect(dayLabelOf(new Date(2026, 9, 5, 9, 4).toISOString(), now)).toBe("어제");
    expect(dayLabelOf(new Date(2026, 9, 3, 9, 4).toISOString(), now)).toBe("10월 3일 (토)");
    expect(shortWhen(new Date(2026, 9, 6, 9, 4).toISOString(), now)).toBe("09:04");
    expect(shortWhen(new Date(2026, 9, 3, 9, 4).toISOString(), now)).toBe("10-03");
  });
});
