import { describe, expect, it } from "vitest";

import { describeNotification, notificationDay, notificationRelationLabel, notificationTime } from "./labels";
import type { Notification } from "./viewModels";

/*
 * WORK-013 WP3-FE — 알림 문장 함수 하나(SPEC-011 §2.1 · §4.2-1 「문장 모양」 · 시안 `handoff/alerts/js/data.js` 의 짜임).
 * 목록과 OS 알림(WP4)이 같은 함수를 쓴다. 사람 이름은 전부 가상이다.
 */

function make(overrides: Partial<Notification>): Notification {
  return {
    notification_id: "n1",
    seq: 1,
    kind: "work.request_received",
    theme: "work",
    item: "request",
    relation: "assignee",
    failure: false,
    actor: { member_id: "m1", display_name: "오지훈 (구성원)" },
    subject: { type: "task", id: "t1", title: "견적서 정리" },
    data: {},
    target: { surface: "work", task_id: "t1" },
    created_at: "2026-10-07T05:00:00Z",
    updated_at: "2026-10-07T05:00:00Z",
    read_at: null,
    ...overrides,
  };
}

const said = (overrides: Partial<Notification>) => describeNotification(make(overrides));

describe("꼬리표 16 — 시안 RELATIONS 그대로", () => {
  it("id 16 과 글자", () => {
    expect(notificationRelationLabel).toEqual({
      assignee: "담당",
      requester: "요청자",
      assigner: "배정자",
      cc: "참조(CC)",
      to: "메일 · 받는 사람",
      "mail-cc": "메일 · 참조",
      "mail-other": "메일",
      dm: "슬랙 · DM",
      mention: "슬랙 · 멘션",
      channel: "슬랙 · 채널",
      "kakao-direct": "카톡 · 1:1",
      "kakao-group": "카톡 · 단체방",
      integration: "내 연동",
      owner: "소유자",
      attendee: "참석자",
      shared: "공유받음",
    });
  });
});

describe("문장 — 종류 19", () => {
  const cases: Array<[string, Partial<Notification>, string, string | null]> = [
    ["work.request_received", {}, "오지훈님이 ‘견적서 정리’ 업무를 요청했습니다", null],
    ["work.request_received 재상신", { data: { resubmitted: true } }, "오지훈님이 ‘견적서 정리’ 업무를 다시 요청했습니다", null],
    ["work.request_answered", { kind: "work.request_answered", relation: "requester", data: { answer: "accepted" } }, "오지훈님이 내가 요청한 ‘견적서 정리’ 업무를 수락했습니다", null],
    ["work.request_answered 협의", { kind: "work.request_answered", relation: "requester", data: { answer: "negotiated" } }, "오지훈님이 내가 요청한 ‘견적서 정리’ 업무에 조건을 제시했습니다", null],
    ["work.assignment_answered", { kind: "work.assignment_answered", relation: "assigner", data: { answer: "accepted" } }, "오지훈님이 내가 배정한 ‘견적서 정리’ 업무의 담당을 수락했습니다", null],
    ["work.assigned 넘김", { kind: "work.assigned", data: { mode: "handed_over" } }, "오지훈님이 ‘견적서 정리’ 업무를 나에게 넘겼습니다", null],
    ["work.assigned 밀려남", { kind: "work.assigned", data: { mode: "displaced", new_assignee_name: "한서윤" } }, "‘견적서 정리’ 업무의 담당이 한서윤님으로 바뀝니다", null],
    ["work.changed 기한", { kind: "work.changed", data: { change: "due_changed", before: "2026-10-08", after: "2026-10-09" } }, "오지훈님이 ‘견적서 정리’ 업무의 기한을 바꿨습니다", "10월 8일(목) → 10월 9일(금)"],
    ["work.proposal_answered", { kind: "work.proposal_answered", relation: "requester", data: { answer: "agreed" } }, "오지훈님이 ‘견적서 정리’ 업무의 제안에 동의했습니다", null],
    ["work.completion_reported", { kind: "work.completion_reported", relation: "requester" }, "오지훈님이 내가 요청한 ‘견적서 정리’ 업무 완료를 보고했습니다", "확인해 주세요 — 승인하거나 보완을 요청한다"],
    ["work.rework_requested", { kind: "work.rework_requested", data: { comment: "단가 근거 표를 붙여 주세요" } }, "오지훈님이 ‘견적서 정리’ 에 보완을 요청했습니다", "「단가 근거 표를 붙여 주세요」"],
    ["work.predecessor_released", { kind: "work.predecessor_released", data: { predecessor_title: "부스 도면 확정" } }, "‘부스 도면 확정’ 업무가 끝나 ‘견적서 정리’ 업무를 시작할 수 있습니다", "선행 업무 완료 · 오지훈님"],
    ["work.commented", { kind: "work.commented", relation: "cc", item: "comment", data: { excerpt: "확인했습니다" } }, "오지훈님이 ‘견적서 정리’ 에 댓글을 남겼습니다", "「확인했습니다」"],
    ["message.mail To", { kind: "message.mail", theme: "message", item: "mail", relation: "to", actor: { external_name: "서지안님" }, data: { subject: "현장 설치 일정표 공유", attachment_count: 2, account: "haram@company.example" } }, "서지안님이 메일 ‘현장 설치 일정표 공유’ 을 보냈습니다", "첨부 2개 · haram@company.example 로 받음"],
    ["message.mail 그 밖", { kind: "message.mail", theme: "message", item: "mail", relation: "mail-other", actor: { external_name: "노을웍스 인사팀" }, data: { subject: "10월 사내 교육 일정 안내", attachment_count: 0 } }, "노을웍스 인사팀이 메일 ‘10월 사내 교육 일정 안내’ 을 보냈습니다", "나는 받는 사람·참조에 없음"],
    ["message.slack 멘션", { kind: "message.slack", theme: "message", item: "slack", relation: "mention", actor: { external_name: "한서윤님" }, data: { room_name: "#pilot-launch", excerpt: "확인 부탁드려요" } }, "한서윤님이 ‘#pilot-launch’ 에서 나를 멘션했습니다", "「확인 부탁드려요」"],
    ["message.slack DM", { kind: "message.slack", theme: "message", item: "slack", relation: "dm", actor: { external_name: "오지훈님" }, data: { excerpt: "통화 가능할까요" } }, "오지훈님이 슬랙 DM 을 보냈습니다", "「통화 가능할까요」"],
    ["message.slack 채널 합침", { kind: "message.slack", theme: "message", item: "slack", relation: "channel", actor: null, data: { room_name: "#design-review", count: 4, senders: ["문다은", "배성민", "오지훈"] } }, "‘#design-review’ 에 새 메시지가 4건 왔습니다", "문다은 · 배성민 외 1명"],
    ["message.slack 채널 합침 — sender_count 우선(검수 W-1)", { kind: "message.slack", theme: "message", item: "slack", relation: "channel", actor: null, data: { room_name: "#design-review", count: 30, senders: ["문다은", "배성민", "오지훈", "한서윤", "서지안"], sender_count: 9 } }, "‘#design-review’ 에 새 메시지가 30건 왔습니다", "문다은 · 배성민 외 7명"],
    ["message.kakao 1:1", { kind: "message.kakao", theme: "message", item: "kakao", relation: "kakao-direct", actor: { external_name: "박지윤님" }, data: { excerpt: "사진 5장" } }, "박지윤님이 카카오톡 메시지를 보냈습니다", "「사진 5장」"],
    ["message.integration_lost", { kind: "message.integration_lost", theme: "message", item: "mail", relation: "integration", failure: true, actor: null, data: { channel: "mail", reason: "disconnected", account: "haram.lab@company.example" } }, "메일 연동 ‘haram.lab@company.example’ 의 연결이 끊겼습니다", "새 메일을 받지 못한다 — 설정에서 다시 연결"],
    ["meeting.invited", { kind: "meeting.invited", theme: "meeting", item: "invite", relation: "attendee", subject: { type: "meeting", id: "g1", title: "파일럿 2차 킥오프" }, data: { starts_at: "2026-10-09T05:00:00Z", ends_at: "2026-10-09T06:00:00Z", place: "3층 회의실" } }, "오지훈님이 ‘파일럿 2차 킥오프’ 회의에 초대했습니다", "10월 9일(금) 14:00 – 15:00 · 3층 회의실"],
    // 서버 모양 — before/after = {starts_at, ends_at, place}(M04 · `_schedule_data` · 검수 F-2)
    ["meeting.changed 시간", { kind: "meeting.changed", theme: "meeting", item: "change", relation: "attendee", subject: { type: "meeting", id: "g1", title: "디자인 리뷰" }, data: { change: "updated", before: { starts_at: "2026-10-08T02:00:00Z", ends_at: "2026-10-08T03:00:00Z", place: "3층 회의실" }, after: { starts_at: "2026-10-08T06:00:00Z", ends_at: "2026-10-08T07:00:00Z", place: "3층 회의실" } } }, "오지훈님이 ‘디자인 리뷰’ 회의 시간을 바꿨습니다", "10월 8일(목) 11:00 → 15:00"],
    ["meeting.changed 다른 날", { kind: "meeting.changed", theme: "meeting", item: "change", relation: "attendee", subject: { type: "meeting", id: "g1", title: "디자인 리뷰" }, data: { change: "updated", before: { starts_at: "2026-10-08T02:00:00Z", ends_at: "2026-10-08T03:00:00Z", place: "3층 회의실" }, after: { starts_at: "2026-10-09T02:00:00Z", ends_at: "2026-10-09T03:00:00Z", place: "3층 회의실" } } }, "오지훈님이 ‘디자인 리뷰’ 회의 시간을 바꿨습니다", "10월 8일(목) 11:00 → 10월 9일(금) 11:00"],
    ["meeting.changed 장소만", { kind: "meeting.changed", theme: "meeting", item: "change", relation: "attendee", subject: { type: "meeting", id: "g1", title: "디자인 리뷰" }, data: { change: "updated", before: { starts_at: "2026-10-08T02:00:00Z", ends_at: "2026-10-08T03:00:00Z", place: "3층 회의실" }, after: { starts_at: "2026-10-08T02:00:00Z", ends_at: "2026-10-08T03:00:00Z", place: "5층 라운지" } } }, "오지훈님이 ‘디자인 리뷰’ 회의 장소를 바꿨습니다", "3층 회의실 → 5층 라운지"],
    ["meeting.changed 시간·장소 둘 다", { kind: "meeting.changed", theme: "meeting", item: "change", relation: "attendee", subject: { type: "meeting", id: "g1", title: "디자인 리뷰" }, data: { change: "updated", before: { starts_at: "2026-10-08T02:00:00Z", ends_at: "2026-10-08T03:00:00Z", place: "3층 회의실" }, after: { starts_at: "2026-10-08T06:00:00Z", ends_at: "2026-10-08T07:00:00Z", place: "5층 라운지" } } }, "오지훈님이 ‘디자인 리뷰’ 회의 시간을 바꿨습니다", "10월 8일(목) 11:00 → 15:00 · 5층 라운지"],
    ["meeting.changed 끝 시각만", { kind: "meeting.changed", theme: "meeting", item: "change", relation: "attendee", subject: { type: "meeting", id: "g1", title: "디자인 리뷰" }, data: { change: "updated", before: { starts_at: "2026-10-08T02:00:00Z", ends_at: "2026-10-08T03:00:00Z", place: "3층 회의실" }, after: { starts_at: "2026-10-08T02:00:00Z", ends_at: "2026-10-08T04:00:00Z", place: "3층 회의실" } } }, "오지훈님이 ‘디자인 리뷰’ 회의 시간을 바꿨습니다", "10월 8일(목) 11:00–12:00 → 11:00–13:00"],
    ["meeting.changed 모양 모름", { kind: "meeting.changed", theme: "meeting", item: "change", relation: "attendee", subject: { type: "meeting", id: "g1", title: "디자인 리뷰" }, data: { change: "updated", before: "x", after: null } }, "오지훈님이 ‘디자인 리뷰’ 회의 정보를 바꿨습니다", null],
    ["meeting.changed 빠짐", { kind: "meeting.changed", theme: "meeting", item: "change", relation: "attendee", subject: { type: "meeting", id: "g1", title: "디자인 리뷰" }, data: { change: "removed" } }, "‘디자인 리뷰’ 회의에서 빠졌습니다", null],
    ["meeting.minutes_ready", { kind: "meeting.minutes_ready", theme: "meeting", item: "minutes", relation: "owner", actor: null, subject: { type: "meeting", id: "g1", title: "주간 기획 회의" }, data: { agenda_count: 4, action_count: 6 } }, "‘주간 기획 회의’ 회의록 정리가 끝났습니다", "안건 4 · 할 일 6"],
    ["meeting.minutes_failed", { kind: "meeting.minutes_failed", theme: "meeting", item: "minutes-fail", relation: "owner", failure: true, actor: null, subject: { type: "meeting", id: "g1", title: "파일럿 회고" } }, "‘파일럿 회고’ 회의록을 만들지 못했습니다", "회의에서 다시 정리할 수 있다"],
    ["meeting.shared", { kind: "meeting.shared", theme: "meeting", item: "share", relation: "shared", subject: { type: "meeting", id: "g1", title: "협력사 미팅" } }, "오지훈님이 ‘협력사 미팅’ 회의를 공유했습니다", null],
  ];
  it.each(cases)("%s", (_name, overrides, text, sub) => {
    const result = said(overrides);
    expect(result.text).toBe(text);
    expect(result.sub).toBe(sub);
  });

  it("19 종류가 모두 「새 알림이 있습니다」 기본 문장이 아니다 · 모르는 종류는 기본 문장으로 깨지지 않는다", () => {
    const kinds = [
      "work.request_received", "work.request_answered", "work.assignment_answered", "work.assigned", "work.changed", "work.proposal_answered",
      "work.completion_reported", "work.rework_requested", "work.predecessor_released", "work.commented",
      "message.mail", "message.slack", "message.kakao", "message.integration_lost",
      "meeting.invited", "meeting.changed", "meeting.minutes_ready", "meeting.minutes_failed", "meeting.shared",
    ];
    expect(kinds).toHaveLength(19);
    for (const kind of kinds) expect(said({ kind }).text, kind).not.toMatch(/새 (알림|소식)/);
    expect(said({ kind: "someday.new", subject: null }).text).toBe("새 알림이 있습니다");
  });

  it("「님」 은 회원에게만 — 외부 발신자(메일·슬랙·카톡) 이름은 받은 그대로(코디 결정)", () => {
    expect(said({ actor: { member_id: "m1", display_name: "오지훈 (구성원)" } }).parts[0]).toEqual({ b: "오지훈님" });
    const mail = said({ kind: "message.mail", theme: "message", relation: "to", actor: { external_name: "서지안" }, data: { subject: "일정표" } });
    expect(mail.parts[0]).toEqual({ b: "서지안" });
    expect(mail.text).toBe("서지안이 메일 ‘일정표’ 을 보냈습니다");
    expect(said({ kind: "message.kakao", theme: "message", relation: "kakao-direct", actor: { external_name: "박지윤" } }).text).toBe("박지윤이 카카오톡 메시지를 보냈습니다");
    expect(said({ kind: "message.slack", theme: "message", relation: "dm", actor: { external_name: "Ji Hoon" } }).text).toBe("Ji Hoon이 슬랙 DM 을 보냈습니다");
  });

  it("누가 · 무엇을은 굵게 갈린다(문장 조각) · OS 제목 = 테마 · 꼬리표 · 가는 곳", () => {
    const result = said({});
    expect(result.parts).toEqual([{ b: "오지훈님" }, "이 ", { q: "견적서 정리" }, " 업무를 요청했습니다"]);
    expect(result.title).toBe("업무 · 담당");
    expect(result.destination).toBe("업무");
    expect(said({ kind: "message.slack", theme: "message", relation: "dm", target: { surface: "inbox", source: "slack", room_id: "r1" } }).destination).toBe("메시지함 · 슬랙");
    expect(said({ kind: "message.integration_lost", theme: "message", relation: "integration", data: { channel: "mail" }, target: { surface: "settings", tab: "mail" } }).destination).toBe("설정 · 메일 연동");
    expect(said({ kind: "meeting.shared", theme: "meeting", target: { surface: "meetings", meeting_id: "g1" } }).destination).toBe("회의");
    // 열 수 없는 줄(target null)도 가는 곳 글자는 종류로 짐작한다
    expect(said({ kind: "message.kakao", theme: "message", relation: "kakao-direct", target: null }).destination).toBe("메시지함 · 카톡");
  });
});

describe("날짜 구분 · 시각 — 서울 기준 (§4.5-2 2)", () => {
  // 지금 = 2026-10-07(수) 15:00 서울
  const now = new Date("2026-10-07T06:00:00Z");
  it("오늘 · 어제 · 이번 주(월요일부터) · 이전 — 자정 경계는 서울이다", () => {
    expect(notificationDay("2026-10-06T15:00:00Z", now)).toBe("today"); // 10-07 00:00 서울
    expect(notificationDay("2026-10-06T14:59:00Z", now)).toBe("yesterday"); // 10-06 23:59 서울
    expect(notificationDay("2026-10-05T00:00:00Z", now)).toBe("week"); // 10-05(월)
    expect(notificationDay("2026-10-04T10:00:00Z", now)).toBe("earlier"); // 10-04(일) — 지난 주
  });
  it("오늘은 상대 시각 · 어제 「어제 HH:MM」 · 이번 주 「M월 D일(요일) HH:MM」 · 이전 「M월 D일(요일)」", () => {
    expect(notificationTime("2026-10-07T05:57:00Z", now)).toBe("3분 전");
    expect(notificationTime("2026-10-07T04:00:00Z", now)).toBe("2시간 전");
    expect(notificationTime("2026-10-07T06:00:00Z", now)).toBe("방금");
    expect(notificationTime("2026-10-06T09:20:00Z", now)).toBe("어제 18:20");
    expect(notificationTime("2026-10-05T08:30:00Z", now)).toBe("10월 5일(월) 17:30");
    expect(notificationTime("2026-09-30T03:00:00Z", now)).toBe("9월 30일(수)");
  });
});
