import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * 카톡 수집기 커맨드 자리 (SPEC-006 v0.6.0 · SPEC-008 §2.5 · WORK-011 FE-b).
 * 실제 커맨드는 SHELL Phase 몫이다 — 여기서는 «부르는 자리»의 판정과 정규화만 잰다.
 */

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...args: unknown[]) => invoke(...args) }));

type ShellWindow = Window & { __TAURI_INTERNALS__?: unknown };

async function loadShell() {
  vi.resetModules();
  return import("./shell");
}

beforeEach(() => {
  invoke.mockReset();
  delete (window as ShellWindow).__TAURI_INTERNALS__;
});

afterEach(() => {
  delete (window as ShellWindow).__TAURI_INTERNALS__;
});

describe("카톡 커맨드", () => {
  it("브라우저(셸 없음)에서는 한 번도 부르지 않고 「없다」", async () => {
    const shell = await loadShell();
    expect(await shell.hasKakaoCollector()).toBe(false);
    expect(await shell.kakaoListRooms()).toEqual({ kind: "absent" });
    expect(await shell.kakaoStoreDeviceToken("t")).toBe("absent");
    expect(invoke).not.toHaveBeenCalled();
  });

  it("커맨드가 없는 셸(개인판)도 「없다」 — 판정은 커맨드가 있는지로 한다", async () => {
    (window as ShellWindow).__TAURI_INTERNALS__ = {};
    invoke.mockRejectedValue(new Error("command kakao_collector_status not found"));
    const shell = await loadShell();
    expect(await shell.hasKakaoCollector()).toBe(false);
    expect(await shell.kakaoListRooms()).toEqual({ kind: "absent" });
  });

  it("앱 웹뷰 — 로컬 방 목록을 정규화하고, 기기 토큰은 응답 없이 넘긴다", async () => {
    (window as ShellWindow).__TAURI_INTERNALS__ = {};
    invoke.mockImplementation(async (command: string) => {
      if (command === "kakao_collector_status") return { app: "on" };
      if (command === "kakao_list_rooms") return { rooms: [{ chatId: 9001, type: "direct", name: "박지윤", members: 2 }, { type: "open", chat_id: "x" }] };
      return undefined;
    });
    const shell = await loadShell();
    expect(await shell.kakaoListRooms()).toEqual({ kind: "ok", rooms: [{ chat_id: "9001", type: "direct", name: "박지윤", member_count: 2 }] });
    expect(await shell.kakaoStoreDeviceToken("secret")).toBe("stored");
    expect(invoke).toHaveBeenCalledWith("kakao_store_device_token", { token: "secret" });
  });

  it("카카오톡이 꺼져 목록을 못 읽으면 kakao_off", async () => {
    (window as ShellWindow).__TAURI_INTERNALS__ = {};
    invoke.mockImplementation(async (command: string) => {
      if (command === "kakao_list_rooms") throw new Error("kakao_off");
      return {};
    });
    const shell = await loadShell();
    expect(await shell.kakaoListRooms()).toEqual({ kind: "kakao_off" });
  });
});
