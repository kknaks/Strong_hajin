import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { currentScreenEpoch, forgetScreenCache, hasScreenValue, recallScreenValue, rememberScreenValue, SCREEN_CACHE_KEYS_PER_PREFIX, scopeScreenCache, useRemembered } from "./screenCache";

/* WORK-008 Phase 2 (B-01) — 화면 데이터 기억의 공용 장치. */
describe("screenCache", () => {
  afterEach(() => scopeScreenCache(null));

  it("주인이 없으면 아무것도 기억하지 않는다 — useState 와 같다", () => {
    const first = renderHook(() => useRemembered<string[]>("k", []));
    act(() => first.result.current[1](["a"]));
    first.unmount();
    const second = renderHook(() => useRemembered<string[]>("k", []));
    expect(second.result.current[0]).toEqual([]);
    expect(second.result.current[2]).toBe(false);
  });

  it("주인이 있으면 setter 로 넣은 값으로 다음 마운트가 시작하고, 그 사실을 알려 준다", () => {
    scopeScreenCache("mina");
    const first = renderHook(() => useRemembered<string[]>("k", []));
    expect(first.result.current[2]).toBe(false);
    act(() => first.result.current[1]((current) => [...current, "a"]));
    first.unmount();
    const second = renderHook(() => useRemembered<string[]>("k", []));
    expect(second.result.current[0]).toEqual(["a"]);
    expect(second.result.current[2]).toBe(true);
  });

  it("초깃값은 기억하지 않는다 — 받은 적 없는 빈 목록이 «받은 값»이 되지 않는다", () => {
    scopeScreenCache("mina");
    renderHook(() => useRemembered<string[]>("k", [])).unmount();
    expect(hasScreenValue("k")).toBe(false);
  });

  it("주인이 바뀌거나 비면(로그아웃) 통째로 버린다", () => {
    scopeScreenCache("mina");
    rememberScreenValue("k", 1, currentScreenEpoch());
    scopeScreenCache("mina");
    expect(recallScreenValue("k")).toBe(1);
    scopeScreenCache("jiho");
    expect(hasScreenValue("k")).toBe(false);
    rememberScreenValue("k", 2, currentScreenEpoch());
    scopeScreenCache(null);
    expect(hasScreenValue("k")).toBe(false);
    scopeScreenCache("jiho");
    expect(hasScreenValue("k")).toBe(false);
  });

  it("forgetScreenCache 는 주인을 둔 채 비운다 — 로그아웃 직전·앱이 새로 설 때", () => {
    scopeScreenCache("mina");
    rememberScreenValue("k", 1, currentScreenEpoch());
    forgetScreenCache();
    expect(hasScreenValue("k")).toBe(false);
  });

  /* fix1 · FAIL-1 — 주인이 바뀐 뒤 도착한 옛 주인의 응답은 새 주인의 기억에 들어가지 않는다. */
  it("요청을 시작한 세대가 지금 세대가 아니면 기억하지 않는다 — 직접 쓰기", () => {
    scopeScreenCache("mina");
    const startedAt = currentScreenEpoch(); // A 가 요청을 띄운다
    scopeScreenCache(null); // A 로그아웃
    scopeScreenCache("jiho"); // B 로그인
    rememberScreenValue("k", "A 의 값", startedAt); // A 의 응답이 늦게 도착
    expect(hasScreenValue("k")).toBe(false);
    rememberScreenValue("k", "B 의 값", currentScreenEpoch());
    expect(recallScreenValue("k")).toBe("B 의 값");
  });

  it("옛 세대에 선 화면의 setter 는 새 주인의 기억에 쓰지 않는다 — useRemembered", () => {
    scopeScreenCache("mina");
    const hookA = renderHook(() => useRemembered<string>("k", ""));
    const setLate = hookA.result.current[1];
    hookA.unmount();
    forgetScreenCache(); // 로그아웃
    scopeScreenCache("jiho");
    act(() => setLate("A 의 값")); // 언마운트된 A 화면에 늦게 온 응답
    expect(hasScreenValue("k")).toBe(false);
    const hookB = renderHook(() => useRemembered<string>("k", ""));
    expect(hookB.result.current[0]).toBe("");
    expect(hookB.result.current[2]).toBe(false);
  });

  /* fix1 · WARN-3 — 키가 갈리는 화면은 접두사마다 최근 N 칸만 남긴다. */
  it("같은 접두사의 칸은 최근 N 개만 남고, 다시 쓴 칸은 최근으로 옮겨진다", () => {
    scopeScreenCache("mina");
    const at = currentScreenEpoch();
    rememberScreenValue("calendar.entries:0", 0, at);
    for (let index = 1; index <= SCREEN_CACHE_KEYS_PER_PREFIX; index += 1) {
      rememberScreenValue(`calendar.entries:${index}`, index, at);
      if (index === 5) rememberScreenValue("calendar.entries:0", 0, at); // 0 을 다시 쓴다 → 최근
    }
    rememberScreenValue("work.tasks", [], at); // 접두사 없는 키는 세지 않는다
    expect(hasScreenValue("calendar.entries:0")).toBe(true);
    expect(hasScreenValue("calendar.entries:1")).toBe(false); // 가장 오래된 칸이 빠졌다
    expect(hasScreenValue(`calendar.entries:${SCREEN_CACHE_KEYS_PER_PREFIX}`)).toBe(true);
    expect(hasScreenValue("work.tasks")).toBe(true);
  });
});
