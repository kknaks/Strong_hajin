import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { forgetViewDetails, isViewingTarget, setViewDetail } from "./currentView";
import { OS_BURST_LIMIT, OS_BURST_WINDOW_MS, createOsNotifier } from "./osNotifier";
import type { Notification, NotificationUpsertedEvent } from "./viewModels";

/*
 * WORK-013 WP4 — 새 알림 → OS 알림의 규칙(SPEC-011 §2.5 · DEC-010 D-39): 새 줄만 · 이어 받은 줄 없음 · 보고 있으면 생략 ·
 * 10초 창에 셋까지 + 「새 알림 N건」. 사람 이름은 가상이다.
 */

function item(id: string, overrides: Partial<Notification> = {}): Notification {
  return {
    notification_id: id,
    seq: 1,
    kind: "work.request_received",
    theme: "work",
    item: "request",
    relation: "assignee",
    failure: false,
    actor: { member_id: "m1", display_name: "오지훈" },
    subject: { type: "task", id: `t-${id}`, title: "견적서 정리" },
    data: {},
    target: { surface: "work", task_id: `t-${id}` },
    created_at: "2026-10-07T05:00:00Z",
    updated_at: "2026-10-07T05:00:00Z",
    read_at: null,
    ...overrides,
  };
}
const event = (notification: Notification, created = true, replayed = false): NotificationUpsertedEvent => ({ v: 1, notification, created, replayed });

let show: ReturnType<typeof vi.fn>;
let viewing: (target: unknown) => boolean;
let foreground = true;

function notifier() {
  return createOsNotifier({ show, isViewing: (target) => viewing(target), isForeground: () => foreground });
}

beforeEach(() => {
  vi.useFakeTimers();
  show = vi.fn();
  viewing = () => false;
  foreground = true;
  forgetViewDetails();
});
afterEach(() => vi.useRealTimers());

describe("무엇을 띄우나", () => {
  it("새 줄은 띄운다 — 제목 = 테마 · 꼬리표 · 본문 = 목록 문장 · target 그대로", () => {
    expect(notifier().offer(event(item("n1")))).toBe("shown");
    expect(show).toHaveBeenCalledWith({ notification_id: "n1", title: "업무 · 담당", body: "오지훈님이 ‘견적서 정리’ 업무를 요청했습니다", target: { surface: "work", task_id: "t-n1" } });
  });

  it("열 수 없는 대상(target: null)도 그대로 싣는다 — 클릭은 목록 줄과 같은 일(검수 W-4)", () => {
    notifier().offer(event(item("n9", { target: null })));
    expect(show).toHaveBeenCalledWith(expect.objectContaining({ notification_id: "n9", target: null }));
  });

  it("합친 줄 갱신(created: false) · 이어 받은 줄(replayed: true)은 띄우지 않는다", () => {
    const os = notifier();
    expect(os.offer(event(item("n1"), false))).toBe("skipped");
    expect(os.offer(event(item("n2"), true, true))).toBe("skipped");
    expect(show).not.toHaveBeenCalled();
  });

  it("앱이 앞에 있고 그 대상을 보고 있으면 생략 · 뒤에 있으면(가려짐·최소화) 보고 있어도 띄운다", () => {
    viewing = () => true;
    expect(notifier().offer(event(item("n1")))).toBe("viewing");
    foreground = false;
    expect(notifier().offer(event(item("n2")))).toBe("shown");
  });
});

describe("몰리면 묶는다 — 10초 창에 셋까지 + 「새 알림 N건」", () => {
  it("넷째부터 세어 두었다가 창 끝에 하나 · 누르면 알림 목록(id null)", () => {
    const os = notifier();
    const results = ["n1", "n2", "n3", "n4", "n5", "n6"].map((id) => os.offer(event(item(id))));
    expect(OS_BURST_LIMIT).toBe(3);
    expect(results).toEqual(["shown", "shown", "shown", "held", "held", "held"]);
    expect(show).toHaveBeenCalledTimes(3);
    vi.advanceTimersByTime(OS_BURST_WINDOW_MS - 1);
    expect(show).toHaveBeenCalledTimes(3);
    vi.advanceTimersByTime(1);
    expect(show).toHaveBeenCalledTimes(4);
    expect(show).toHaveBeenLastCalledWith({ notification_id: null, title: "알림", body: "새 알림 3건", target: { surface: "notifications" } });
  });

  it("셋 이하면 묶음이 없다 · 창이 끝나면 새 창이 열린다", () => {
    const os = notifier();
    os.offer(event(item("n1")));
    os.offer(event(item("n2")));
    vi.advanceTimersByTime(OS_BURST_WINDOW_MS);
    expect(show).toHaveBeenCalledTimes(2);
    expect(["n3", "n4", "n5"].map((id) => os.offer(event(item(id))))).toEqual(["shown", "shown", "shown"]);
  });

  it("생략·건너뛴 것은 창 수에 들지 않는다", () => {
    const os = notifier();
    os.offer(event(item("n0"), false));
    viewing = (target) => (target as { task_id?: string }).task_id === "t-n1";
    os.offer(event(item("n1")));
    expect(["n2", "n3", "n4"].map((id) => os.offer(event(item(id))))).toEqual(["shown", "shown", "shown"]);
  });
});

describe("지금 보고 있나 (currentView)", () => {
  it("업무 · 요청 · 메일 · 방 · 회의 · 설정 탭 — 화면 종류와 상세가 둘 다 맞아야", () => {
    setViewDetail("task", "t1");
    expect(isViewingTarget({ surface: "work", task_id: "t1" }, "work")).toBe(true);
    expect(isViewingTarget({ surface: "work", task_id: "t1" }, "inbox")).toBe(false);
    expect(isViewingTarget({ surface: "work", task_id: "t2" }, "work")).toBe(false);
    setViewDetail("workRequest", "r1");
    expect(isViewingTarget({ surface: "work", work_request_id: "r1" }, "work")).toBe(true);
    setViewDetail("inboxMail", "m1");
    expect(isViewingTarget({ surface: "inbox", source: "mail", message_id: "m1" }, "inbox")).toBe(true);
    setViewDetail("inboxRoom", "room1");
    expect(isViewingTarget({ surface: "inbox", source: "slack", room_id: "room1", message_id: "x" }, "inbox")).toBe(true);
    setViewDetail("meeting", "g1");
    expect(isViewingTarget({ surface: "meetings", meeting_id: "g1" }, "meetings")).toBe(true);
    setViewDetail("settingsTab", "slack");
    expect(isViewingTarget({ surface: "settings", tab: "slack" }, "settings")).toBe(true);
    expect(isViewingTarget({ surface: "settings", tab: "mail" }, "settings")).toBe(false);
    // 알림 목록을 보고 있는 것은 대상이 아니다(OQ-1111)
    expect(isViewingTarget({ surface: "notifications" }, "notifications")).toBe(false);
    setViewDetail("task", null);
    expect(isViewingTarget({ surface: "work", task_id: "t1" }, "work")).toBe(false);
  });
});
