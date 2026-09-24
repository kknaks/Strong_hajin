/**
 * WORK-006 Phase 1 — 계측 페이지가 셸에게 말을 거는 **유일한 자리**.
 *
 * 제품의 `src/lib/shell.ts` 는 **Phase 3·4 의 몫**이다. 이 파일은 그 자리를 미리 차지하지 않고,
 * 계측 페이지 안에서만 산다. 제품 화면에는 한 줄도 배선하지 않는다.
 *
 * 지키는 계약 (SPEC-006 §4)
 * - 커맨드는 **넷**이다. 다섯째를 만들지 않는다(I-2)
 * - 셸이 없으면 **단 한 번도 호출하지 않는다**(`E-01` · S-13) — 브라우저에서 열었을 때
 *   에러도 경고도 뜨지 않아야 한다
 * - **TTL·주기 갱신·타이머가 없다**(I-3). 재요청은 «사건»이 있을 때만 한다
 */

export type WakeState = "off" | "on" | "degraded";

export type ShellInfo = {
  shell_api: number;
  app_version: string;
  platform: string;
  features: string[];
};

/** 셸이 웹에게 주는 것 넷. 테스트는 여기에 가짜를 꽂는다. */
export type ShellCommands = {
  shellInfo(): Promise<ShellInfo>;
  wakeGuardAcquire(input: { session: string; reason: string }): Promise<{ state: WakeState }>;
  wakeGuardRelease(input: { session: string }): Promise<{ state: WakeState }>;
  openExternal(input: { url: string }): Promise<void>;
};

type TauriWindow = Window & { __TAURI_INTERNALS__?: unknown };

/**
 * 셸 존재 판정. **커맨드가 아니다** — 셸 전용 전역이 있는지로만 본다(SPEC-006 §4).
 * 여기서 `false` 면 호출을 **시도조차 하지 않는다**.
 */
export function hasShell(scope: TauriWindow = window): boolean {
  return typeof scope.__TAURI_INTERNALS__ !== "undefined";
}

/** 실제 IPC. `@tauri-apps/api` 의 `invoke` 를 늦게 불러온다 — 브라우저에서는 부르지 않는다. */
export function tauriCommands(): ShellCommands {
  const call = async <T,>(command: string, args?: Record<string, unknown>): Promise<T> => {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<T>(command, args);
  };
  return {
    shellInfo: () => call<ShellInfo>("shell_info"),
    wakeGuardAcquire: (input) => call<{ state: WakeState }>("wake_guard_acquire", { ...input }),
    wakeGuardRelease: (input) => call<{ state: WakeState }>("wake_guard_release", { ...input }),
    openExternal: (input) => call<void>("open_external", { ...input }),
  };
}

/**
 * 녹음 1회를 가리키는 키. **회차마다 새 값**이고 회의 id 를 쓰지 않는다(SPEC-006 §4 Validation).
 *
 * 회의 id 처럼 회차를 넘어 같은 값을 쓰면 `E-08`·`E-09` 의 보호 — 「지난 녹음의 늦은 해제가
 * 지금 도는 녹음을 풀지 못한다」 — 가 통째로 사라진다. AC-T42 가 보는 것이 이 줄이다.
 */
export function newSessionKey(): string {
  return crypto.randomUUID();
}

export type GuardClient = {
  acquire(session: string, reason: string): Promise<{ state: WakeState }>;
  release(session: string): Promise<{ state: WakeState }>;
};

/**
 * 세션 키 하나에 대해 **호출을 직렬화**하는 얇은 껍질.
 *
 * 왜 필요한가 — SPEC-006 §4 「늦게 도착한 획득은 걸지 않는다」. 획득 요청이 날아가는 중에
 * 화면이 정리되면, 해제가 먼저 도착하고 **획득이 뒤에 도착해 점유가 남는다.**
 * 같은 세션의 호출을 한 줄로 세우면 해제가 **언제나 획득 뒤**에 가므로 최종 잔존이 0 이다.
 * SPEC 이 수단을 지정하지 않고 **결과(잔존 0)만** 요구하므로(I-7), 직렬화를 고른다 —
 * **영구 기록(tombstone)도 타이머도 두지 않는다**.
 */
export function createGuardClient(commands: ShellCommands): GuardClient {
  // 세션별 마지막 작업. 끝나면 지워 누수를 남기지 않는다.
  const chains = new Map<string, Promise<unknown>>();

  const enqueue = <T,>(session: string, job: () => Promise<T>): Promise<T> => {
    const previous = chains.get(session);
    // 줄이 비어 있으면 **곧바로** 나간다. 첫 호출까지 마이크로태스크로 미루면
    // 마이크가 열린 시점과 점유가 서는 시점 사이가 괜히 벌어진다(L-01 은 «그 시점»이다).
    // 앞선 호출이 실패해도 줄은 이어진다 — 실패 하나가 뒤의 해제를 막으면 점유가 남는다.
    const result = previous ? previous.then(job, job) : job();
    const settled = result.then(
      () => undefined,
      () => undefined,
    );
    chains.set(session, settled);
    // 내 뒤에 아무도 붙지 않았으면 자리를 비운다 — 끝난 녹음의 키를 들고 있지 않는다.
    void settled.then(() => {
      if (chains.get(session) === settled) chains.delete(session);
    });
    return result;
  };

  return {
    acquire: (session, reason) => enqueue(session, () => commands.wakeGuardAcquire({ session, reason })),
    release: (session) => enqueue(session, () => commands.wakeGuardRelease({ session })),
  };
}
