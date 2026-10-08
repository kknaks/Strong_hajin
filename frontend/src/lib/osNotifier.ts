import { describeNotification } from "./labels";
import type { NotifyShowInput } from "./shell";
import type { NotificationTarget, NotificationUpsertedEvent } from "./viewModels";

/**
 * 새 알림 → OS 알림 (WORK-013 WP4 · SPEC-011 §2.5 · DEC-010 D-39). 앱이 켜져 있을 때 사건 채널로 온 것만 — 셸은 웹이 넘긴 것만 띄운다.
 *
 * 띄우지 않거나 묶는 셋:
 * 1. **보고 있으면 생략** — 앱 창이 앞에 있고(`visibilityState = "visible"` 이면서 `document.hasFocus()`) 지금 화면이 그 알림의
 *    대상이면(`isViewing`) 띄우지 않는다. 알림 목록을 보고 있는 것은 대상이 아니다(OQ-1111)
 * 2. **새 줄만** — `created: false`(합친 슬랙 채널 줄 갱신)는 없다 · **이어 받기로 온 줄**(`replayed: true`)도 없다
 * 3. **몰리면 묶는다** — 첫 OS 알림부터 **10초 창**에 **셋까지** 낱낱이 띄우고, 넷째부터는 세어 두었다가 창이 끝날 때
 *    「새 알림 N건」 하나(`notification_id: null` · 누르면 알림 목록)를 띄운다. 창 · 문턱은 이 파일의 상수(실측 뒤 조정)
 *
 * 글자는 목록과 같은 함수(`describeNotification` — 제목 = 「테마 · 꼬리표」 · 본문 = 문장 · 보조 줄은 싣지 않는다).
 */
export const OS_BURST_WINDOW_MS = 10_000;
export const OS_BURST_LIMIT = 3;

export const osNotificationCopy = {
  bundleTitle: "알림",
  bundleBody: (count: number) => `새 알림 ${count}건`,
} as const;

export type OsNotifierDeps = {
  show: (input: NotifyShowInput) => unknown;
  /** 지금 화면이 그 대상인가(App 의 화면 종류 + `currentView`). */
  isViewing: (target: NotificationTarget) => boolean;
  /** 앱 창이 앞에 있나 — 기본은 문서가 보이고 포커스가 있을 때. */
  isForeground?: () => boolean;
};

export type OsOffer = "shown" | "held" | "skipped" | "viewing";

export function createOsNotifier(deps: OsNotifierDeps) {
  const foreground = deps.isForeground ?? (() => document.visibilityState === "visible" && document.hasFocus());
  let windowOpen = false;
  let shown = 0;
  let held = 0;
  let timer: number | null = null;

  const flush = () => {
    timer = null;
    windowOpen = false;
    if (held > 0) {
      deps.show({ notification_id: null, title: osNotificationCopy.bundleTitle, body: osNotificationCopy.bundleBody(held), target: { surface: "notifications" } });
    }
    shown = 0;
    held = 0;
  };

  return {
    offer(event: NotificationUpsertedEvent): OsOffer {
      if (!event.created || event.replayed) return "skipped";
      const item = event.notification;
      if (item.target && foreground() && deps.isViewing(item.target)) return "viewing";
      if (!windowOpen) {
        windowOpen = true;
        timer = window.setTimeout(flush, OS_BURST_WINDOW_MS);
      }
      if (shown < OS_BURST_LIMIT) {
        shown += 1;
        const said = describeNotification(item);
        // 열 수 없는 대상(`target: null`)도 그대로 싣는다 — 누르면 목록 줄과 같은 일(읽음 + 「열 수 없는 항목입니다」 · 검수 W-4)
        deps.show({ notification_id: item.notification_id, title: said.title, body: said.text, target: item.target });
        return "shown";
      }
      held += 1;
      return "held";
    },
    dispose() {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      windowOpen = false;
      shown = 0;
      held = 0;
    },
  };
}
