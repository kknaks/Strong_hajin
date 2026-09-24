import { describe, expect, it, vi } from "vitest";
import { createGuardClient, hasShell, newSessionKey, type ShellCommands, type WakeState } from "./probeShell";

function commandSpy() {
  const calls: string[] = [];
  const pending: Array<() => void> = [];
  const commands: ShellCommands = {
    shellInfo: async () => ({ shell_api: 1, app_version: "0.0.1", platform: "macos", features: [] }),
    wakeGuardAcquire: ({ session }) =>
      new Promise<{ state: WakeState }>((resolve) => {
        calls.push(`acquire:${session}`);
        pending.push(() => resolve({ state: "on" }));
      }),
    wakeGuardRelease: ({ session }) =>
      new Promise<{ state: WakeState }>((resolve) => {
        calls.push(`release:${session}`);
        pending.push(() => resolve({ state: "off" }));
      }),
    openExternal: async () => undefined,
  };
  return { calls, pending, commands };
}

describe("셸 존재 판정 (E-01 · S-13)", () => {
  it("셸 전용 전역이 없으면 없는 것으로 본다", () => {
    expect(hasShell({} as Window)).toBe(false);
  });

  it("전역이 있으면 있는 것으로 본다", () => {
    expect(hasShell({ __TAURI_INTERNALS__: {} } as unknown as Window)).toBe(true);
  });
});

describe("세션 키 (AC-T42)", () => {
  it("부를 때마다 다른 값이다 — 회차를 넘어 같은 값을 쓰지 않는다", () => {
    const keys = new Set(Array.from({ length: 50 }, () => newSessionKey()));
    expect(keys.size).toBe(50);
  });

  it("128 자 한도 안이다 (SPEC §4 Validation)", () => {
    expect(newSessionKey().length).toBeLessThanOrEqual(128);
    expect(newSessionKey().length).toBeGreaterThan(0);
  });
});

describe("같은 세션의 호출 직렬화 (I-7 · AC-T40)", () => {
  it("획득이 날아가는 중에 해제를 걸어도 해제가 뒤에 간다", async () => {
    const { calls, pending, commands } = commandSpy();
    const guard = createGuardClient(commands);
    const session = "s-1";

    const acquired = guard.acquire(session, "회의 녹음 중");
    // 아직 응답이 오기 전에 화면이 정리돼 해제가 걸린다.
    const released = guard.release(session);

    // 해제가 앞질러 가지 않았다 — 아직 획득만 나갔다.
    expect(calls).toEqual(["acquire:s-1"]);

    pending.shift()?.();
    await acquired;
    await vi.waitFor(() => expect(calls).toEqual(["acquire:s-1", "release:s-1"]));
    pending.shift()?.();
    await expect(released).resolves.toEqual({ state: "off" });
  });

  it("획득이 실패해도 뒤의 해제가 막히지 않는다", async () => {
    const calls: string[] = [];
    const guard = createGuardClient({
      shellInfo: async () => ({ shell_api: 1, app_version: "0.0.1", platform: "macos", features: [] }),
      wakeGuardAcquire: async () => {
        calls.push("acquire");
        throw new Error("IPC 실패");
      },
      wakeGuardRelease: async () => {
        calls.push("release");
        return { state: "off" as WakeState };
      },
      openExternal: async () => undefined,
    });

    await expect(guard.acquire("s-1", "회의 녹음 중")).rejects.toThrow("IPC 실패");
    await expect(guard.release("s-1")).resolves.toEqual({ state: "off" });
    expect(calls).toEqual(["acquire", "release"]);
  });

  it("다른 세션은 서로를 기다리지 않는다", async () => {
    const { calls, pending, commands } = commandSpy();
    const guard = createGuardClient(commands);
    void guard.acquire("s-1", "회의 녹음 중");
    void guard.acquire("s-2", "회의 녹음 중");
    expect(calls).toEqual(["acquire:s-1", "acquire:s-2"]);
    pending.forEach((resolve) => resolve());
  });

  it("끝난 세션의 자리를 들고 있지 않는다", async () => {
    const { pending, commands } = commandSpy();
    const guard = createGuardClient(commands);
    const acquired = guard.acquire("s-1", "회의 녹음 중");
    pending.shift()?.();
    await acquired;
    const released = guard.release("s-1");
    await vi.waitFor(() => expect(pending.length).toBe(1));
    pending.shift()?.();
    await released;
    // 새 호출이 앞선 줄에 매이지 않고 곧바로 나간다.
    const again = guard.acquire("s-1", "회의 녹음 중");
    await vi.waitFor(() => expect(pending.length).toBe(1));
    pending.shift()?.();
    await expect(again).resolves.toEqual({ state: "on" });
  });
});
