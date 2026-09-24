/**
 * 데스크톱 셸(Tauri)과 말하는 **유일한 자리**.
 *
 * `api.ts` 밖에서 `fetch` 하지 않는다는 규약과 같은 결이다 — **이 파일 밖에서 `invoke` 를
 * 부르지 않는다.** 여는 것은 SPEC-006 §4 의 커맨드 넷뿐이고, 범용 invoke·파일 권한·
 * 운영 origin 을 여기에 더하지 않는다.
 *
 * ## 셸이 없으면 «한 번도» 부르지 않는다 (`E-01` · S-13)
 * 브라우저에서 열었을 때 동작이 지금과 완전히 같아야 한다 — 에러도 경고도 뜨지 않는다.
 * 그래서 전역 유무로 먼저 판정하고, 없으면 호출 자체를 만들지 않는다.
 *
 * ## 여기에 타이머가 없다 (WORK I-3)
 * TTL·주기 갱신·참조계수를 두지 않는다. 재요청은 **사건이 있을 때만** 일어난다
 * (문서가 다시 보이게 될 때 — L-14). 셸은 신호가 없다고 해서 점유를 풀지 않는다.
 */

export type WakeState = "off" | "on" | "degraded";

export type ShellInfo = {
  shell_api: number;
  app_version: string;
  platform: string;
  features: string[];
};

/** 점유 요청의 결과. **실패해도 녹음을 막지 않는다** — 화면이 U-3 을 낼 값일 뿐이다. */
export type WakeOutcome =
  | { kind: "on" }
  /** OS 에 걸지 못했거나(`E-02`·`E-03`) 커맨드 호출이 실패했다(`E-14a`). 녹음은 계속된다. */
  | { kind: "degraded" }
  /** 셸이 없다(`E-01`). **아무것도 보이지 않아야 한다.** */
  | { kind: "absent" };

/** 해제 결과. 실패는 `E-14b` — 녹음을 다시 켜지 않고 «정리 실패» 사실만 드러낸다. */
export type ReleaseOutcome = { kind: "released" } | { kind: "failed" } | { kind: "absent" };

/** 외부 링크를 셸이 처리했는가. **실패를 «성공»으로 뭉개지 않는다**(리뷰 W-6). */
export type OpenExternalOutcome = "opened" | "absent" | "failed";

/**
 * 정리에 실패한 세션 키. **React state 가 아니다** — 화면이 이미 사라진 뒤(언마운트)에도
 * 사실이 남아야 하기 때문이다(리뷰 W-3).
 *
 * 언마운트 정리에서 `wake_guard_release` 가 실패하면 그 화면은 이미 없어 `setState` 로는
 * 아무것도 드러낼 수 없다. 그래서 **모듈에 사실을 적고 콘솔에도 한 줄 남긴다.**
 * 이것은 **점유를 되살리지 않고**, 녹음을 다시 켜지도 않는다 — 기록일 뿐이다.
 */
const cleanupFailures = new Set<string>();

/** 진단·시험용. 정리에 실패한 세션 키들. */
export function wakeGuardCleanupFailures(): string[] {
  return [...cleanupFailures];
}

function recordCleanupFailure(session: string) {
  if (cleanupFailures.has(session)) return;
  cleanupFailures.add(session);
  // 창을 닫으면 네이티브가 정리한다(L-06) — 그래서 «남을 수 있다»까지만 적는다.
  console.warn(`[shell] 절전 방지 정리에 실패했습니다(E-14b). session=${session}`);
}

type ShellWindow = Window & { __TAURI_INTERNALS__?: unknown };

/**
 * 셸 존재 판정. **커맨드가 아니다** — 셸 전용 전역이 있는지로만 본다(SPEC-006 §4).
 * 판정에 실패해 호출이 나가더라도 `E-01` 로 떨어져 녹음을 막지 않는다.
 */
export function hasShell(scope: ShellWindow = window): boolean {
  return typeof scope.__TAURI_INTERNALS__ !== "undefined";
}

/**
 * 셸 API 모듈을 **한 번만** 불러온다.
 *
 * 처음엔 호출마다 `await import(...)` 했는데, **동시에 두 호출이 나가면 두 번째 import 가
 * 실패**해 멀쩡한 획득이 `degraded` 로 떨어지는 것을 시험에서 관측했다. 획득과 해제가
 * 겹치거나 두 녹음이 동시에 도는 순간이 바로 그 경우라, 약속을 하나로 모아 공유한다.
 *
 * 실패하면 캐시를 비워 **다음 시도가 다시 해 볼 수 있게** 둔다 — 한 번의 실패로 셸 기능이
 * 영구히 죽지 않는다.
 */
let corePromise: Promise<typeof import("@tauri-apps/api/core")> | null = null;

function core(): Promise<typeof import("@tauri-apps/api/core")> {
  if (!corePromise) {
    corePromise = import("@tauri-apps/api/core").catch((error: unknown) => {
      corePromise = null;
      throw error;
    });
  }
  return corePromise;
}

async function call<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  // 셸이 없는 브라우저에서는 이 모듈을 **불러오지도 않는다** — 부르는 쪽이 먼저 막는다.
  const { invoke } = await core();
  return invoke<T>(command, args);
}

/** 셸이 무엇을 할 수 있는지. 실패해도 기능 부재로 단정하지 않는다(SPEC §4). */
export async function shellInfo(): Promise<ShellInfo | null> {
  if (!hasShell()) return null;
  try {
    return await call<ShellInfo>("shell_info");
  } catch {
    // `E-14a` — 「모른다」이지 「없다」가 아니다. 녹음 시작 때 획득을 그냥 시도한다.
    return null;
  }
}

/**
 * 녹음 1회를 가리키는 키. **회차마다 새 값**이고 회의 id 를 쓰지 않는다.
 *
 * 회의 id 처럼 회차를 넘어 같은 값을 쓰면 「지난 녹음의 늦은 해제가 지금 도는 녹음을 풀지
 * 못한다」(`E-08`)는 보호가 통째로 사라진다.
 */
export function newWakeSession(): string {
  return crypto.randomUUID();
}

/**
 * 세션 키 하나에 대해 호출을 **한 줄로 세운다.**
 *
 * 획득이 날아가는 중에 화면이 정리되면 해제가 먼저 도착하고 **획득이 뒤에 도착해 점유가
 * 남는다.** 같은 세션의 호출을 직렬화하면 해제가 언제나 획득 뒤에 가므로 최종 잔존이 0 이다
 * (SPEC §4 「늦게 도착한 획득은 걸지 않는다」 — 수단은 구현이 고른다).
 * **영구 기록도 타이머도 두지 않는다.**
 */
const chains = new Map<string, Promise<unknown>>();

function enqueue<T>(session: string, job: () => Promise<T>): Promise<T> {
  const previous = chains.get(session);
  // 앞선 호출이 실패해도 줄은 이어진다 — 실패 하나가 뒤의 해제를 막으면 점유가 남는다.
  const result = previous ? previous.then(job, job) : job();
  const settled = result.then(
    () => undefined,
    () => undefined,
  );
  chains.set(session, settled);
  void settled.then(() => {
    if (chains.get(session) === settled) chains.delete(session);
  });
  return result;
}

/**
 * **마이크가 «실제로» 열린 시점에** 부른다(L-01 · L-07). 연결 시도나 준비 신호가 아니다.
 *
 * 같은 키로 다시 불러도 점유가 늘지 않는다 — 그 시점에 OS 절전 방지가 풀려 있으면
 * 셸이 다시 건다(`E-09` 재무장). 그래서 재요청은 «거는» 방향이지 «푸는» 방향이 아니다.
 */
export async function acquireWakeGuard(session: string, reason = "회의 녹음 중"): Promise<WakeOutcome> {
  if (!hasShell()) return { kind: "absent" };
  try {
    const reply = await enqueue(session, () =>
      call<{ state: WakeState }>("wake_guard_acquire", { session, reason }),
    );
    return reply.state === "on" ? { kind: "on" } : { kind: "degraded" };
  } catch {
    // `E-14a` — 셸은 있는데 호출이 실패했다. **degraded 와 같게 보고 녹음을 계속한다.**
    return { kind: "degraded" };
  }
}

/** **녹음이 «실제로» 끝난 자리에서** 부른다(L-02 · L-05 · L-08 · L-10). 멱등이다(`E-08`). */
export async function releaseWakeGuard(session: string): Promise<ReleaseOutcome> {
  if (!hasShell()) return { kind: "absent" };
  try {
    await enqueue(session, () => call<{ state: WakeState }>("wake_guard_release", { session }));
    return { kind: "released" };
  } catch {
    // `E-14b` — **이미 끝낸 녹음을 다시 켜지 않는다.** degraded(=녹음 계속)로 뭉개지 않는다.
    // 화면이 이미 사라졌을 수도 있으므로(L-10 언마운트) **여기서 사실을 남긴다**(W-3).
    recordCleanupFailure(session);
    return { kind: "failed" };
  }
}

/**
 * 외부 링크를 OS 기본 브라우저로 넘긴다(U-4). 셸이 없으면 **지금 그대로** 웹이 연다.
 * 반환값은 「셸이 처리했는가」이고, `false` 면 부르는 쪽이 기존 경로를 쓴다.
 */
export async function openExternal(url: string): Promise<OpenExternalOutcome> {
  if (!hasShell()) return "absent";
  try {
    await call<void>("open_external", { url });
    return "opened";
  } catch {
    /* `E-14c` — **같은 커맨드를 다시 시도하지 않는다.** 그렇다고 웹 폴백(`window.open`)으로
       넘기지도 않는다: 셸 안에서 그것은 **앱 창 안에 두 번째 웹뷰가 앉는** 길이고,
       U-4 가 막으려는 바로 그 사고다.
       **그러나 조용히 삼키지는 않는다**(리뷰 W-6) — 실패를 그대로 돌려주고 기록을 남긴다. */
    console.warn(`[shell] 외부 링크를 열지 못했습니다(E-14c). url=${url}`);
    return "failed";
  }
}
