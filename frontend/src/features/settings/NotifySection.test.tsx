import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { NotificationSettings } from "../../lib/viewModels";
import { NotifySection } from "./NotifySection";
import { Switch } from "./settingsParts";

/* WORK-013 WP3-FE — 설정 → 알림 설정(SPEC-011 §2.3 · §4.4 · 시안 `settings.v1.jsx` `NotifySection` · design-change-3·4). */

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const DEFAULTS = (): NotificationSettings => ({
  enabled: true,
  themes: {
    work: { on: true, items: { request: true, assign: true, answer: true, report: true, rework: true, change: true, comment: false, unblock: true } },
    message: { on: true, items: { mail: true, slack: true, kakao: true } },
    meeting: { on: true, items: { invite: true, change: true, minutes: true, "minutes-fail": true, share: true } },
  },
  version: 0,
});

let stored: NotificationSettings;
let puts: NotificationSettings[] = [];
let putReply: ((body: NotificationSettings) => Response | Promise<Response>) | null = null;
const onError = vi.fn();

beforeEach(() => {
  stored = DEFAULTS();
  puts = [];
  putReply = null;
  onError.mockClear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path !== "/api/me/notification-settings") return json({ detail: "없음" }, 404);
      if ((init?.method ?? "GET") === "GET") return json(stored);
      const body = JSON.parse(String(init?.body)) as NotificationSettings;
      puts.push(body);
      if (putReply) return putReply(body);
      stored = { ...body, version: body.version + 1 };
      return json(stored);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const card = (title: string) => within(screen.getByRole("region", { name: title }));

describe("알림 설정 — 시안 그대로", () => {
  it("intro · 「알림 받기」 · 테마 카드 셋(업무 8 · 메시지 3 · 회의 5 = 16) · 문구 · 「N개 중 M개」 · 메시지 note · 받는 경로 카드 없음", async () => {
    render(<NotifySection onError={onError} />);
    expect(await screen.findByText("받을 알림을 고른다. 앱이 켜져 있으면 시스템 알림으로도 뜬다.")).toBeTruthy();
    expect(screen.getByRole("switch", { name: "알림 받기" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText("아래에서 고른 알림을 받는다")).toBeTruthy();
    expect(card("업무").getAllByRole("checkbox")).toHaveLength(8);
    expect(card("메시지").getAllByRole("checkbox")).toHaveLength(3);
    expect(card("회의").getAllByRole("checkbox")).toHaveLength(5);
    expect(screen.getAllByRole("checkbox")).toHaveLength(16);
    expect(card("업무").getByText("8개 중 7개")).toBeTruthy(); // 댓글만 기본 꺼짐
    expect(card("메시지").getByText("3개 중 3개")).toBeTruthy();
    expect(card("회의").getByText("5개 중 5개")).toBeTruthy();
    expect(card("업무").getByText("내가 보낸 요청·배정·제안에 답이 왔을 때")).toBeTruthy(); // design-change-4
    expect(card("업무").getByText("내 업무의 기한·조건이 바뀌거나 취소·재개되면")).toBeTruthy(); // design-change-3
    expect(card("메시지").getByText("연동이 끊기면 해당 채널 알림으로 알려 준다")).toBeTruthy();
    expect(screen.getByRole("switch", { name: "업무 알림" })).toBeTruthy();
    expect(screen.queryByText("받는 경로")).toBeNull();
  });

  it("체크를 바꾸면 바로 저장한다 — 전체 모양 + 읽은 version · 다음 저장은 새 version", async () => {
    render(<NotifySection onError={onError} />);
    const comment = (await screen.findByText("내 업무에 댓글")).closest("label")!.querySelector("input")!;
    fireEvent.click(comment);
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].themes.work.items.comment).toBe(true);
    expect(puts[0].version).toBe(0);
    await waitFor(() => expect(card("업무").getByText("8개 중 8개")).toBeTruthy());
    fireEvent.click(screen.getByRole("switch", { name: "회의 알림" }));
    await waitFor(() => expect(puts).toHaveLength(2));
    expect(puts[1].version).toBe(1);
    expect(puts[1].themes.meeting.on).toBe(false);
    expect(await card("회의").findByText("꺼짐")).toBeTruthy();
    expect(onError).not.toHaveBeenCalled();
  });

  it("전체를 끄면 문구가 바뀌고 테마 스위치 · 체크가 모두 비활성 — 아래 값은 그대로 저장된다", async () => {
    render(<NotifySection onError={onError} />);
    fireEvent.click(await screen.findByRole("switch", { name: "알림 받기" }));
    expect(screen.getByText("알림을 하나도 받지 않는다 — 아래 고른 값은 그대로 남는다")).toBeTruthy();
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].enabled).toBe(false);
    expect(puts[0].themes.work.items.request).toBe(true);
    await waitFor(() => expect((screen.getByRole("switch", { name: "알림 받기" }) as HTMLButtonElement).disabled).toBe(false));
    for (const box of screen.getAllByRole("checkbox")) expect((box as HTMLInputElement).disabled).toBe(true);
    for (const name of ["업무 알림", "메시지 알림", "회의 알림"]) expect((screen.getByRole("switch", { name }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("region", { name: "업무" }).classList.contains("scax-set-notify--off")).toBe(true);
  });

  it("테마를 끄면 그 항목만 흐려지고 잠긴다 — 다른 테마는 그대로", async () => {
    render(<NotifySection onError={onError} />);
    fireEvent.click(await screen.findByRole("switch", { name: "메시지 알림" }));
    await waitFor(() => expect(puts).toHaveLength(1));
    await waitFor(() => expect((card("메시지").getAllByRole("checkbox")[0] as HTMLInputElement).disabled).toBe(true));
    expect((card("업무").getAllByRole("checkbox")[0] as HTMLInputElement).disabled).toBe(false);
  });

  it("저장 중에는 그 칸만 잠근다 · 실패하면 그 칸을 되돌리고 「알림 설정을 저장하지 못했습니다」", async () => {
    let release: (response: Response) => void = () => undefined;
    putReply = () => new Promise<Response>((resolve) => (release = resolve));
    render(<NotifySection onError={onError} />);
    const mail = (await screen.findByText("메일", { selector: ".scax-set-row__name" })).closest("label")!.querySelector("input")!;
    fireEvent.click(mail);
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(mail.disabled).toBe(true);
    expect(mail.checked).toBe(false);
    expect((card("메시지").getAllByRole("checkbox")[1] as HTMLInputElement).disabled).toBe(false); // 다른 칸은 그대로
    release(json({ detail: "서버 오류" }, 500));
    await waitFor(() => expect(onError).toHaveBeenCalledWith("알림 설정을 저장하지 못했습니다"));
    await waitFor(() => expect(mail.checked).toBe(true));
    expect(mail.disabled).toBe(false);
  });

  it("409(다른 창이 먼저 저장) — 알리고 서버 값을 다시 읽는다", async () => {
    render(<NotifySection onError={onError} />);
    const slack = (await screen.findByText("슬랙", { selector: ".scax-set-row__name" })).closest("label")!.querySelector("input")!;
    // 다른 창이 먼저 카톡을 끄고 version 을 올렸다
    stored = { ...DEFAULTS(), themes: { ...DEFAULTS().themes, message: { on: true, items: { mail: true, slack: true, kakao: false } } }, version: 3 };
    putReply = () => json({ detail: { code: "SETTINGS_VERSION_CONFLICT" } }, 409);
    fireEvent.click(slack);
    await waitFor(() => expect(onError).toHaveBeenCalledWith("알림 설정을 저장하지 못했습니다"));
    await waitFor(() => expect(card("메시지").getByText("3개 중 2개")).toBeTruthy());
    const kakao = screen.getByText("카톡", { selector: ".scax-set-row__name" }).closest("label")!.querySelector("input")!;
    expect(kakao.checked).toBe(false);
    expect(slack.checked).toBe(true);
  });

  it("못 불러오면 오류와 「다시 시도」", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "x" }, 500)));
    render(<NotifySection onError={onError} />);
    expect(await screen.findByText("알림 설정을 불러오지 못했습니다")).toBeTruthy();
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeTruthy();
  });
});

describe("Switch (설정 부품 — DS 에 Toggle 이 없다)", () => {
  it("role=switch · aria-checked · 이름 · 누르면 onToggle · 꺼진 모양과 켜진 모양", () => {
    const onToggle = vi.fn();
    const { rerender } = render(<Switch label="알림 받기" on={false} onToggle={onToggle} />);
    const button = screen.getByRole("switch", { name: "알림 받기" });
    expect(button.getAttribute("aria-checked")).toBe("false");
    expect(button.className).toBe("scax-switch");
    expect(button.querySelector(".scax-switch__knob")).not.toBeNull();
    fireEvent.click(button);
    expect(onToggle).toHaveBeenCalledTimes(1);
    rerender(<Switch label="알림 받기" on onToggle={onToggle} />);
    expect(button.getAttribute("aria-checked")).toBe("true");
    expect(button.classList.contains("scax-switch--on")).toBe(true);
    rerender(<Switch disabled label="알림 받기" on onToggle={onToggle} />);
    fireEvent.click(button);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
