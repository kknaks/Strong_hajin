import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * `shell.ts` 단위 검증 — Phase 4 검수가 요구한 자리들.
 *
 * 여기서 재는 것은 **셸이 없을 때의 침묵**(`E-01`), **`degraded`·호출 실패가 녹음을 막지
 * 않는다**(`E-14a`), **같은 키의 직렬화**(늦은 획득의 잔존 0), **해제 실패가 사실로 남는다**
 * (`E-14b`)다. 브라우저 기본 경로를 깨지 않는 것이 첫 번째 계약이라 그것부터 본다.
 */

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...args: unknown[]) => invoke(...args) }));

type ShellWindow = Window & { __TAURI_INTERNALS__?: unknown };

function attachShell() {
  (window as ShellWindow).__TAURI_INTERNALS__ = {};
}

function detachShell() {
  delete (window as ShellWindow).__TAURI_INTERNALS__;
}

/** 모듈 안의 직렬화 줄·정리 기록이 시험끼리 새지 않게 매번 새로 불러온다. */
async function loadShell() {
  vi.resetModules();
  return import("./shell");
}

beforeEach(() => {
  invoke.mockReset();
  detachShell();
});

afterEach(() => {
  detachShell();
  vi.restoreAllMocks();
});

describe("셸이 없을 때 (E-01 · S-13)", () => {
  it("커맨드를 한 번도 부르지 않는다", async () => {
    const shell = await loadShell();
    expect(shell.hasShell()).toBe(false);

    await expect(shell.acquireWakeGuard("s1")).resolves.toEqual({ kind: "absent" });
    await expect(shell.releaseWakeGuard("s1")).resolves.toEqual({ kind: "absent" });
    await expect(shell.shellInfo()).resolves.toBeNull();
    await expect(shell.openExternal("https://example.test/")).resolves.toBe("absent");

    // 브라우저 기본 경로가 깨지지 않는다는 것의 관측 가능한 형태다.
    expect(invoke).not.toHaveBeenCalled();
  });
});

describe("획득 (L-01 · L-07)", () => {
  beforeEach(attachShell);

  it("on 이면 그대로 돌려준다", async () => {
    const shell = await loadShell();
    invoke.mockResolvedValueOnce({ state: "on" });
    await expect(shell.acquireWakeGuard("s1")).resolves.toEqual({ kind: "on" });
    expect(invoke).toHaveBeenCalledWith("wake_guard_acquire", {
      session: "s1",
      reason: "회의 녹음 중",
    });
  });

  it("degraded 는 degraded 로 — 던지지 않는다 (E-02·E-03)", async () => {
    const shell = await loadShell();
    invoke.mockResolvedValueOnce({ state: "degraded" });
    await expect(shell.acquireWakeGuard("s1")).resolves.toEqual({ kind: "degraded" });
  });

  it("호출이 실패해도 degraded 로 답하고 던지지 않는다 (E-14a)", async () => {
    // 녹음 경로가 예외로 깨지면 「실패해도 녹음은 계속된다」가 무너진다.
    const shell = await loadShell();
    invoke.mockRejectedValueOnce(new Error("IPC 실패"));
    await expect(shell.acquireWakeGuard("s1")).resolves.toEqual({ kind: "degraded" });
  });
});

describe("같은 키의 직렬화 — 늦은 획득의 최종 잔존 0", () => {
  beforeEach(attachShell);

  it("획득이 날아가는 중에 해제를 걸어도 해제가 뒤에 간다", async () => {
    const shell = await loadShell();
    const order: string[] = [];
    let finishAcquire: (() => void) | null = null;

    invoke.mockImplementation((command: string) => {
      order.push(command);
      if (command === "wake_guard_acquire") {
        return new Promise((resolve) => {
          finishAcquire = () => resolve({ state: "on" });
        });
      }
      return Promise.resolve({ state: "off" });
    });

    const acquired = shell.acquireWakeGuard("s1");
    const released = shell.releaseWakeGuard("s1");

    // `call()` 이 `@tauri-apps/api` 를 **동적으로** 불러오므로 첫 호출도 비동기다 —
    // 동기 시점에 단정하지 않고 첫 호출이 나가기를 기다린다.
    await vi.waitFor(() => expect(order).toEqual(["wake_guard_acquire"]));
    // 해제가 앞질러 가지 않았다 — 획득이 아직 끝나지 않아 줄에서 대기 중이다.
    expect(order).toEqual(["wake_guard_acquire"]);

    finishAcquire!();
    await acquired;
    await released;
    expect(order).toEqual(["wake_guard_acquire", "wake_guard_release"]);
  });

  it("획득이 실패해도 뒤의 해제가 막히지 않는다", async () => {
    // 실패 하나가 해제를 막으면 점유가 그대로 남는다.
    const shell = await loadShell();
    const order: string[] = [];
    invoke.mockImplementation((command: string) => {
      order.push(command);
      return command === "wake_guard_acquire"
        ? Promise.reject(new Error("IPC 실패"))
        : Promise.resolve({ state: "off" });
    });

    await expect(shell.acquireWakeGuard("s1")).resolves.toEqual({ kind: "degraded" });
    await expect(shell.releaseWakeGuard("s1")).resolves.toEqual({ kind: "released" });
    expect(order).toEqual(["wake_guard_acquire", "wake_guard_release"]);
  });

  it("다른 키는 서로를 기다리지 않는다", async () => {
    const shell = await loadShell();
    const order: string[] = [];
    invoke.mockImplementation((_command: string, args: { session: string }) => {
      order.push(args.session);
      return new Promise(() => {}); // 끝나지 않게 둔다 — 줄서기 여부만 본다.
    });
    void shell.acquireWakeGuard("a");
    void shell.acquireWakeGuard("b");
    // 둘 다 나갔다 — 서로를 기다렸다면 하나만 나가 있을 것이다.
    await vi.waitFor(() => expect(order).toEqual(["a", "b"]));
  });
});

describe("해제 실패 (E-14b)", () => {
  beforeEach(attachShell);

  it("failed 를 돌려주고 사실을 «화면 밖»에 남긴다", async () => {
    // 언마운트 정리에서는 화면이 이미 없어 setState 로 드러낼 수 없다(리뷰 W-3).
    const shell = await loadShell();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    invoke.mockRejectedValueOnce(new Error("IPC 실패"));

    await expect(shell.releaseWakeGuard("s-late")).resolves.toEqual({ kind: "failed" });

    expect(shell.wakeGuardCleanupFailures()).toContain("s-late");
    expect(warn).toHaveBeenCalled();
  });

  it("같은 세션을 두 번 실패해도 기록은 하나다", async () => {
    const shell = await loadShell();
    vi.spyOn(console, "warn").mockImplementation(() => {});
    invoke.mockRejectedValue(new Error("IPC 실패"));

    await shell.releaseWakeGuard("s-twice");
    await shell.releaseWakeGuard("s-twice");

    expect(shell.wakeGuardCleanupFailures().filter((key) => key === "s-twice")).toHaveLength(1);
  });

  it("성공한 해제는 기록을 남기지 않는다", async () => {
    const shell = await loadShell();
    invoke.mockResolvedValueOnce({ state: "off" });
    await expect(shell.releaseWakeGuard("s-ok")).resolves.toEqual({ kind: "released" });
    expect(shell.wakeGuardCleanupFailures()).not.toContain("s-ok");
  });
});

describe("세션 키 (E-08 보호의 전제)", () => {
  it("부를 때마다 다른 값이고 128자 한도 안이다", async () => {
    const shell = await loadShell();
    const keys = new Set(Array.from({ length: 50 }, () => shell.newWakeSession()));
    expect(keys.size).toBe(50);
    expect(shell.newWakeSession().length).toBeLessThanOrEqual(128);
  });
});

describe("외부 링크 (U-4 · W-6)", () => {
  beforeEach(attachShell);

  it("성공하면 opened 다", async () => {
    const shell = await loadShell();
    invoke.mockResolvedValueOnce(undefined);
    await expect(shell.openExternal("https://example.test/")).resolves.toBe("opened");
    expect(invoke).toHaveBeenCalledWith("open_external", { url: "https://example.test/" });
  });

  it("실패를 성공으로 뭉개지 않는다 — failed 를 돌려준다", async () => {
    // 옛 구현은 실패해도 true 를 돌려줘 **링크가 조용히 아무 일도 안 했다**(리뷰 W-6).
    const shell = await loadShell();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    invoke.mockRejectedValueOnce(new Error("IPC 실패"));
    await expect(shell.openExternal("https://example.test/")).resolves.toBe("failed");
    expect(warn).toHaveBeenCalled();
  });
});

describe("첨부 저장 결과 사건 (U-5 5 · OQ-T12)", () => {
  function fire(detail: unknown) {
    window.dispatchEvent(new CustomEvent("strong-hajin:download", { detail }));
  }

  it("셸이 없으면 듣지 않는다 — 브라우저 동작 그대로 (AC-T48)", async () => {
    const shell = await loadShell();
    const handler = vi.fn();
    const stop = shell.onShellDownload(handler);
    fire({ ok: true, filename: "회의록.html" });
    stop();
    expect(handler).not.toHaveBeenCalled();
  });

  it("성공은 파일 이름과 함께, 실패는 실패로 넘긴다", async () => {
    attachShell();
    const shell = await loadShell();
    const handler = vi.fn();
    const stop = shell.onShellDownload(handler);
    fire({ ok: true, filename: "회의록 (1).html" });
    fire({ ok: true, filename: null });
    fire({ ok: false, filename: null });
    stop();
    expect(handler.mock.calls).toEqual([
      [{ ok: true, filename: "회의록 (1).html" }],
      [{ ok: true, filename: null }],
      [{ ok: false }],
    ]);
  });

  it("모양이 다른 사건은 버리고, 해제하면 더 듣지 않는다", async () => {
    attachShell();
    const shell = await loadShell();
    const handler = vi.fn();
    const stop = shell.onShellDownload(handler);
    fire(null);
    fire({ ok: "yes" });
    fire("저장됨");
    stop();
    fire({ ok: true, filename: "a.html" });
    expect(handler).not.toHaveBeenCalled();
  });

  it("사건 이름은 셸과 맞춘 상수 하나다", async () => {
    const shell = await loadShell();
    expect(shell.SHELL_DOWNLOAD_EVENT).toBe("strong-hajin:download");
  });
});

/*
 * WORK-013 WP4 — OS 알림 다리(SPEC-011 §4.6 · SPEC-006 v0.7.0). `features` 에 `notification` 이 있을 때만 부른다.
 */
describe("OS 알림 — 셸이 없거나 기능이 없으면 부르지 않는다", () => {
  it("셸이 없으면 absent · false · 듣지 않음 — invoke 0", async () => {
    const shell = await loadShell();
    await expect(shell.hasShellNotifications()).resolves.toBe(false);
    await expect(shell.notifyPermission(true)).resolves.toBe("absent");
    await expect(shell.notifyShow({ notification_id: "n1", title: "t", body: "b", target: { surface: "work", task_id: "t1" } })).resolves.toBe(false);
    const handler = vi.fn();
    shell.onShellNotificationClick(handler)();
    expect(invoke).not.toHaveBeenCalled();
  });

  it("옛 dmg(features 에 notification 없음)는 shell_info 만 묻고 알림 커맨드는 부르지 않는다", async () => {
    attachShell();
    const shell = await loadShell();
    invoke.mockResolvedValueOnce({ shell_api: 1, app_version: "0.0.1", platform: "macos", features: ["wake_guard", "open_external"] });
    await expect(shell.notifyPermission(true)).resolves.toBe("absent");
    await expect(shell.notifyShow({ notification_id: null, title: "알림", body: "새 알림 4건", target: { surface: "notifications" } })).resolves.toBe(false);
    expect(invoke.mock.calls.map(([name]) => name)).toEqual(["shell_info"]);
  });
});

describe("OS 알림 — 커맨드 둘", () => {
  beforeEach(attachShell);
  const withFeature = () => invoke.mockResolvedValueOnce({ shell_api: 1, app_version: "0.0.1", platform: "macos", features: ["wake_guard", "open_external", "notification"] });

  it("notify_permission — 인자 {request} · 셋으로 갈린다 · 실패는 default(막지 않음)", async () => {
    const shell = await loadShell();
    withFeature();
    invoke.mockResolvedValueOnce({ state: "granted" });
    await expect(shell.notifyPermission(true)).resolves.toBe("granted");
    expect(invoke).toHaveBeenLastCalledWith("notify_permission", { request: true });
    invoke.mockResolvedValueOnce({ state: "denied" });
    await expect(shell.notifyPermission(false)).resolves.toBe("denied");
    invoke.mockResolvedValueOnce({ state: "default" });
    await expect(shell.notifyPermission(false)).resolves.toBe("default");
    invoke.mockRejectedValueOnce(new Error("IPC"));
    await expect(shell.notifyPermission(true)).resolves.toBe("default");
    // shell_info 는 한 번만 물었다(기억)
    expect(invoke.mock.calls.filter(([name]) => name === "shell_info")).toHaveLength(1);
  });

  it("notify_show — 인자는 SPEC 이름 그대로(snake_case) · shown 그대로 · 거부·실패는 false", async () => {
    const shell = await loadShell();
    withFeature();
    const input = { notification_id: "n1", title: "업무 · 담당", body: "오지훈님이 ‘견적서’ 업무를 요청했습니다", target: { surface: "work" as const, task_id: "t1" } };
    invoke.mockResolvedValueOnce({ shown: true });
    await expect(shell.notifyShow(input)).resolves.toBe(true);
    expect(invoke).toHaveBeenLastCalledWith("notify_show", input);
    invoke.mockResolvedValueOnce({ shown: false });
    await expect(shell.notifyShow(input)).resolves.toBe(false);
    invoke.mockRejectedValueOnce(new Error("검증 실패"));
    await expect(shell.notifyShow(input)).resolves.toBe(false);
  });
});

describe("OS 알림 클릭 사건 (셸 → 웹)", () => {
  beforeEach(attachShell);

  it("detail 의 notification_id · target 을 넘긴다 · 묶음은 id null · 모양이 다르면 버린다 · 해제하면 더 듣지 않는다", async () => {
    const shell = await loadShell();
    const handler = vi.fn();
    const off = shell.onShellNotificationClick(handler);
    const fire = (detail: unknown) => window.dispatchEvent(new CustomEvent(shell.SHELL_NOTIFICATION_CLICK_EVENT, { detail }));
    fire({ notification_id: "n1", target: { surface: "work", task_id: "t1" } });
    fire({ notification_id: null, target: { surface: "notifications" } });
    fire({ notification_id: "n9", target: null }); // 열 수 없는 대상 — 검수 W-4
    fire({ notification_id: 3, target: {} });
    fire("x");
    expect(handler.mock.calls).toEqual([
      [{ notification_id: "n1", target: { surface: "work", task_id: "t1" } }],
      [{ notification_id: null, target: { surface: "notifications" } }],
      [{ notification_id: "n9", target: null }],
    ]);
    off();
    fire({ notification_id: "n2", target: { surface: "work", task_id: "t2" } });
    expect(handler).toHaveBeenCalledTimes(3);
  });

  it("사건 이름은 셸과 맞춘 상수 하나다", async () => {
    const shell = await loadShell();
    expect(shell.SHELL_NOTIFICATION_CLICK_EVENT).toBe("strong-hajin:notification-click");
  });
});
