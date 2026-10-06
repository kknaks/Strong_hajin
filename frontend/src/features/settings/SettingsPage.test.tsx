import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useCallback, useState, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Integration, OrganizationProfile } from "../../lib/viewModels";
import { SettingsPage, type SettingsTab } from "./SettingsPage";

/* 설정 (WORK-011 FE-b · SPEC-008 §2.3~2.6 · AC-01~06·14·17~20) — 서버·셸은 가짜다. */

const shell = vi.hoisted(() => ({
  inApp: false,
  rooms: { kind: "ok", rooms: [] as unknown[] } as unknown,
  store: vi.fn(async (_token: string) => "stored"),
}));
vi.mock("../../lib/shell", () => ({
  hasKakaoCollector: async () => shell.inApp,
  kakaoListRooms: async () => (shell.inApp ? shell.rooms : { kind: "absent" }),
  kakaoStoreDeviceToken: (token: string) => shell.store(token),
  openExternal: async () => "absent",
}));
const consent = vi.hoisted(() => ({ begin: vi.fn(async () => "browser") }));
vi.mock("./consent", () => ({ beginConsent: (...args: unknown[]) => consent.begin(...(args as [])) }));

const json = (body: unknown, status = 200) => new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const integ = (over: Partial<Integration>): Integration => ({
  id: "i",
  kind: "mail",
  status: "connected",
  display_name: "",
  synced_count: 0,
  last_synced_at: null,
  backfill_count: 0,
  collector: null,
  ...over,
});

let integrations: Integration[] = [];
let calls: Array<{ path: string; method: string; body: unknown }> = [];
let overrides: Record<string, () => Response> = {};

const session: OrganizationProfile = {
  member_id: "haram",
  display_name: "유하람",
  organizations: [{ id: "o1", name: "기획팀" }],
  capabilities: [],
  assistant_character: { character_key: "cream-cat", version: 1 },
  profile_image_url: null,
};

function Harness({ tab = "mail", onChoose = noop, onImage = noop }: { tab?: SettingsTab; onChoose?: (key: string) => void; onImage?: (url: string | null) => void }) {
  const [rails, setRails] = useState<{ left?: ReactNode }>({});
  const [title, setTitle] = useState<string | null>(null);
  const registerRails = useCallback((next: { left?: ReactNode }) => setRails(next), []);
  const registerTitle = useCallback((next: string | null) => setTitle(next), []);
  return (
    <>
      <h1>{title}</h1>
      <div data-testid="rail">{rails.left}</div>
      <SettingsPage
        characterBusy={false}
        characterError={null}
        initialTab={tab}
        onChooseCharacter={onChoose}
        onError={notices.error}
        onNotice={notices.notice}
        onProfileImage={onImage}
        onRegisterRails={registerRails}
        onRegisterTitle={registerTitle}
        session={session}
      />
    </>
  );
}
const noop = () => undefined;
const notices = { error: vi.fn(), notice: vi.fn() };

beforeEach(() => {
  integrations = [];
  calls = [];
  overrides = {};
  shell.inApp = false;
  shell.rooms = { kind: "ok", rooms: [] };
  shell.store.mockClear();
  consent.begin.mockClear();
  notices.error.mockClear();
  notices.notice.mockClear();
  vi.stubGlobal("WebSocket", undefined);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method, body: init?.body });
      const key = `${method} ${path}`;
      if (overrides[key]) return overrides[key]();
      if (path === "/api/integrations") return json(integrations);
      if (path.endsWith("/connect") || path.endsWith("/reconnect")) return json({ authorize_url: "https://accounts.example/consent", state: "s" });
      if (path.startsWith("/api/integrations/slack/available-rooms"))
        return json({
          rooms: [
            { room_id: "C01", type: "channel", name: "#pilot-launch", is_bot: false, member_count: 14, already_added: true },
            { room_id: "C03", type: "channel", name: "#general", is_bot: false, member_count: 52, already_added: false },
            { room_id: "D03", type: "dm", name: "질문 도우미", is_bot: true, member_count: 2, already_added: false },
            { room_id: "G02", type: "group_dm", name: "한서윤, 오지훈", is_bot: false, member_count: 3, already_added: false },
          ],
          next_cursor: null,
        });
      if (/\/api\/integrations\/[^/]+\/rooms$/.test(path) && method === "GET") {
        if (path.includes("i-slack")) return json([{ room_id: "r1", external_id: "C01", type: "channel", name: "#pilot-launch", member_count: 14, synced_count: 4902, status: "live" }]);
        if (path.includes("i-kakao")) return json([{ room_id: "k1", external_id: "9001", type: "direct", name: "박지윤", member_count: 2, synced_count: 2318, status: "live" }]);
        return json([]);
      }
      if (path === "/api/device-tokens" && method === "GET") return json([{ id: "d1", device_name: "MacBook Pro", created_at: "2026-10-06T00:00:00Z", last_used_at: null }]);
      if (path === "/api/device-tokens" && method === "POST") return json({ token: "SECRET-TOKEN" }, 201);
      if (path === "/api/profile/image" && method === "PUT") return json({ profile_image_url: "/api/profile/image?v=1" });
      // 실제 서버처럼 202 + 빈 본문(FE 수정 판 4)
      if (method === "POST" && path.endsWith("/rooms")) return new Response(null, { status: 202 });
      return json(null, 204);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("메일 연동", () => {
  it("연결 전 — 「Google로 연결」은 서버가 준 동의 URL 로 간다(F-2)", async () => {
    render(<Harness />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("메일 연동");
    fireEvent.click(await screen.findByRole("button", { name: "Google로 연결" }));
    await waitFor(() => expect(consent.begin).toHaveBeenCalledWith({ authorize_url: "https://accounts.example/consent", state: "s" }));
    expect(calls.some((call) => call.method === "POST" && call.path === "/api/integrations/mail/connect")).toBe(true);
  });

  it("계정 여럿 · 실시간/과거 채우는 중/연결 끊김(다시 연결) · 연결 해제는 확인 뒤", async () => {
    integrations = [
      integ({ id: "m1", display_name: "haram@company.example", synced_count: 1284, last_synced_at: "2026-10-06T00:10:00Z" }),
      integ({ id: "m2", display_name: "lab@company.example", status: "backfilling", backfill_count: 312 }),
      integ({ id: "m3", display_name: "old@company.example", status: "disconnected" }),
    ];
    render(<Harness />);
    const card = await screen.findByRole("region", { name: "연결된 메일 계정" });
    expect(within(card).getByText("3개")).toBeTruthy();
    expect(within(card).getByText("과거 메일 채우는 중 · 312건")).toBeTruthy();
    expect(within(card).getByText(/토큰이 만료됐거나 Google 에서 권한을 거둔/)).toBeTruthy();
    fireEvent.click(within(card).getByRole("button", { name: "다시 연결" }));
    await waitFor(() => expect(calls.some((call) => call.path === "/api/integrations/m3/reconnect")).toBe(true));
    expect(within(card).getByRole("button", { name: "다른 계정 연결" })).toBeTruthy();

    fireEvent.click(within(card).getAllByRole("button", { name: "연결 해제" })[0]);
    const dialog = screen.getByRole("dialog", { name: "메일 계정 연결 해제" });
    expect(dialog.textContent).toContain("haram@company.example 에서 더 이상 메일을 받지 않는다.");
    fireEvent.click(within(dialog).getByRole("button", { name: "연결 해제" }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.path === "/api/integrations/m1/disconnect")).toBe(true));
  });
});

describe("슬랙 연동", () => {
  it("수집 방 · 방 고르기 창(그룹 DM 실명 · 봇 「앱」 · 추가됨) · 선택한 방 추가", async () => {
    integrations = [integ({ id: "i-slack", kind: "slack", display_name: "노을웍스" })];
    render(<Harness tab="slack" />);
    const rooms = await screen.findByRole("region", { name: "수집 방" });
    expect(await within(rooms).findByText("#pilot-launch")).toBeTruthy();
    expect(within(rooms).getByText("채널 · 공개 · 적재 4,902건")).toBeTruthy();
    fireEvent.click(within(rooms).getByRole("button", { name: "방 추가" }));
    const picker = await screen.findByRole("dialog", { name: "방 추가" });
    expect(await within(picker).findByText("한서윤, 오지훈")).toBeTruthy();
    expect(within(picker).getByText("앱")).toBeTruthy();
    expect(within(picker).getByText("추가됨")).toBeTruthy();
    expect((within(picker).getByRole("checkbox", { name: "#pilot-launch" }) as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(within(picker).getByRole("checkbox", { name: "#general" }));
    fireEvent.click(within(picker).getByRole("checkbox", { name: "한서윤, 오지훈" }));
    fireEvent.click(within(picker).getByRole("button", { name: "선택한 2개 추가" }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.path === "/api/integrations/i-slack/rooms")).toBe(true));
    const body = JSON.parse(String(calls.find((call) => call.method === "POST" && call.path === "/api/integrations/i-slack/rooms")!.body));
    expect(body.room_ids).toEqual(["C03", "G02"]);
    // 빈 본문 202 는 성공이다 — 오류 토스트가 없고 창이 닫힌다
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "방 추가" })).toBeNull());
    expect(notices.error).not.toHaveBeenCalled();
  });

  it("끊긴 워크스페이스 — 모든 방이 「멈춤」, 「연결 해제」는 확인 뒤", async () => {
    integrations = [integ({ id: "i-slack", kind: "slack", status: "disconnected", display_name: "노을웍스" })];
    render(<Harness tab="slack" />);
    expect(await screen.findByText(/슬랙 연결이 끊겨 모든 방의 수집이 멈췄다/)).toBeTruthy();
    expect(await screen.findByText("멈춤")).toBeTruthy();
    expect(screen.getByText("멈춤 — 연결 끊김")).toBeTruthy();
  });
});

describe("카카오톡 연동 — 앱 웹뷰 / 브라우저", () => {
  const kakao = (collector: Partial<NonNullable<Integration["collector"]>> | null) =>
    integ({
      id: "i-kakao",
      kind: "kakao",
      collector: collector
        ? { app_state: "on", device_name: "MacBook Pro", app_version: "1.2.0", kakao_state: "running", kakao_reason: null, account_name: "유하람", account_changed: false, reported_at: null, ...collector }
        : null,
    });

  it("연동이 없으면 「Mac 앱 받기」(GitHub Releases)", async () => {
    render(<Harness tab="kakao" />);
    expect((await screen.findByRole("link", { name: "Mac 앱 받기" })).getAttribute("href")).toBe("https://github.com/kknaks/Strong_hajin/releases");
  });

  it("브라우저 — 방 추가 창은 없고 안내만, 고른 방 목록·빼기는 된다(서버 정본)", async () => {
    integrations = [kakao({})];
    render(<Harness tab="kakao" />);
    const rooms = await screen.findByRole("region", { name: "수집 방" });
    expect(await within(rooms).findByText("박지윤")).toBeTruthy();
    expect(within(rooms).queryByRole("button", { name: "방 추가" })).toBeNull();
    expect(within(rooms).getByText(/방 추가는 Mac 앱에서 고릅니다/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "이 Mac 연결" })).toBeNull();
    fireEvent.click(within(rooms).getByRole("button", { name: "빼기" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "방 빼기" })).getByRole("button", { name: "빼기" }));
    await waitFor(() => expect(calls.some((call) => call.method === "DELETE" && call.path === "/api/integrations/i-kakao/rooms/k1")).toBe(true));
  });

  it("앱 웹뷰 — 로컬 방 목록으로 고르고 서버에 저장 · 「이 Mac 연결」 토큰은 셸로만 가고 화면에 남지 않는다", async () => {
    shell.inApp = true;
    shell.rooms = { kind: "ok", rooms: [{ chat_id: "9001", type: "direct", name: "박지윤", member_count: 2 }, { chat_id: "9007", type: "group", name: "파일럿 2차 TF", member_count: 7 }] };
    integrations = [kakao({})];
    render(<Harness tab="kakao" />);
    const rooms = await screen.findByRole("region", { name: "수집 방" });
    fireEvent.click(await within(rooms).findByRole("button", { name: "방 추가" }));
    const picker = await screen.findByRole("dialog", { name: "카카오톡 방 추가" });
    expect(await within(picker).findByText("추가됨")).toBeTruthy();
    fireEvent.click(within(picker).getByRole("checkbox", { name: "파일럿 2차 TF" }));
    fireEvent.click(within(picker).getByRole("button", { name: "선택한 1개 추가" }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.path === "/api/integrations/i-kakao/rooms")).toBe(true));
    const body = JSON.parse(String(calls.find((call) => call.method === "POST" && call.path === "/api/integrations/i-kakao/rooms")!.body));
    expect(body).toEqual({ room_ids: ["9007"], rooms: [{ room_id: "9007", type: "group", name: "파일럿 2차 TF", member_count: 7 }] });

    fireEvent.click(screen.getByRole("button", { name: "이 Mac 연결" }));
    await waitFor(() => expect(shell.store).toHaveBeenCalledWith("SECRET-TOKEN"));
    expect(JSON.parse(String(calls.find((call) => call.method === "POST" && call.path === "/api/device-tokens")!.body))).toEqual({ device_name: "MacBook Pro" });
    expect(document.body.textContent).not.toContain("SECRET-TOKEN");
    expect(notices.notice).toHaveBeenCalledWith("이 Mac 을 연결했습니다.");
  });

  it("상태 카드 — 앱 꺼짐 · 읽기 불가(권한) 문구 · 계정 바뀜 「다시 연결」 = reset-account", async () => {
    integrations = [kakao({ app_state: "off", reported_at: "2026-10-06T00:00:00Z" })];
    render(<Harness tab="kakao" />);
    expect(await screen.findByText("멈춤 — 데스크톱 앱 꺼짐")).toBeTruthy();
    expect(screen.getByText("알 수 없음")).toBeTruthy();
    cleanup();

    integrations = [kakao({ kakao_state: "unreadable", kakao_reason: "permission" })];
    render(<Harness tab="kakao" />);
    expect(await screen.findByText("전체 디스크 접근을 켜 주세요")).toBeTruthy();
    cleanup();

    integrations = [kakao({ account_changed: true })];
    render(<Harness tab="kakao" />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("계정이 바뀌었습니다");
    fireEvent.click(within(alert).getByRole("button", { name: "다시 연결" }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.path === "/api/integrations/kakao/reset-account")).toBe(true));
  });

  it("기기 토큰 — 마지막 사용 · 철회(브라우저에서도)", async () => {
    integrations = [kakao({})];
    render(<Harness tab="kakao" />);
    const card = await screen.findByRole("region", { name: "이 Mac 연결" });
    expect(await within(card).findByText(/마지막 사용 아직 없음/)).toBeTruthy();
    fireEvent.click(within(card).getByRole("button", { name: "철회" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "기기 연결 철회" })).getByRole("button", { name: "철회" }));
    await waitFor(() => expect(calls.some((call) => call.method === "DELETE" && call.path === "/api/device-tokens/d1")).toBe(true));
  });
});

describe("프로필 설정", () => {
  it("이름·소속·직책·직무는 읽기 전용 · 이미지는 고르면 바로 저장(1MB 초과는 거절) · 캐릭터는 고르면 저장", async () => {
    const onImage = vi.fn();
    const onChoose = vi.fn();
    render(<Harness onChoose={onChoose} onImage={onImage} tab="account" />);
    const me = screen.getByRole("region", { name: "내 정보" });
    expect(within(me).getByText("유하람")).toBeTruthy();
    expect(within(me).getByText("기획팀")).toBeTruthy();
    expect(within(me).queryByRole("textbox")).toBeNull();

    const image = screen.getByRole("region", { name: "프로필 이미지" });
    const big = new File(["x"], "IMG_2041.jpg", { type: "image/jpeg" });
    Object.defineProperty(big, "size", { value: 2.4 * 1024 * 1024 });
    fireEvent.change(within(image).getByLabelText("이미지 변경"), { target: { files: [big] } });
    expect(within(image).getByRole("alert").textContent).toContain("2.4MB");
    const small = new File(["x"], "me.png", { type: "image/png" });
    fireEvent.change(within(image).getByLabelText("이미지 변경"), { target: { files: [small] } });
    await waitFor(() => expect(onImage).toHaveBeenCalledWith("/api/profile/image?v=1"));

    const characters = screen.getByRole("region", { name: "AX 캐릭터" });
    expect(within(characters).getAllByRole("radio")).toHaveLength(9);
    fireEvent.click(within(characters).getByRole("radio", { name: /레서판다/ }));
    expect(onChoose).toHaveBeenCalledWith("red-panda");
  });

  it("비밀번호 — 불일치는 화면에서 막고, 현재 틀림(401)은 그 칸에", async () => {
    overrides["POST /api/profile/password"] = () => json({ detail: { code: "current_password_incorrect" } }, 401);
    render(<Harness tab="account" />);
    const card = screen.getByRole("region", { name: "비밀번호 변경" });
    fireEvent.change(within(card).getByLabelText("현재 비밀번호"), { target: { value: "wrongpass" } });
    fireEvent.change(within(card).getByLabelText("새 비밀번호"), { target: { value: "newpass2026!" } });
    fireEvent.change(within(card).getByLabelText("새 비밀번호 확인"), { target: { value: "newpass2025!" } });
    expect(within(card).getByText("새 비밀번호가 서로 다릅니다.")).toBeTruthy();
    expect((within(card).getByRole("button", { name: "비밀번호 변경" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(card).getByLabelText("새 비밀번호 확인"), { target: { value: "newpass2026!" } });
    fireEvent.click(within(card).getByRole("button", { name: "비밀번호 변경" }));
    expect(await within(card).findByText("현재 비밀번호가 맞지 않습니다.")).toBeTruthy();
    expect(JSON.parse(String(calls.find((call) => call.path === "/api/profile/password")!.body))).toEqual({ current: "wrongpass", new: "newpass2026!" });
  });

  it("알림 설정은 범위 밖이라 메뉴 자리만 서고 눌리지 않는다(D-37)", async () => {
    render(<Harness tab="account" />);
    const rail = within(screen.getByTestId("rail"));
    expect((rail.getByRole("button", { name: "알림 설정" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(rail.getByRole("button", { name: "카카오톡 연동" }));
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("카카오톡 연동");
  });
});
