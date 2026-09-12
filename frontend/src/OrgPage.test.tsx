import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OrgPage } from "./OrgPage";

afterEach(cleanup);

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" }, status });

const UNITS = [
  { id: "the-sc", name: "더에쓰씨", parent_id: null, unit_type: "회사", lifecycle: "active", display_order: 0, member_count: 3, direct_member_count: 0, leaders: [] },
  {
    id: "support",
    name: "경영지원본부",
    parent_id: "the-sc",
    unit_type: "본부",
    lifecycle: "active",
    display_order: 1,
    member_count: 3,
    direct_member_count: 1,
    leaders: [{ display_name: "윤도현", position: "본부장", kind: "primary" }],
  },
  { id: "finance", name: "재무회계팀", parent_id: "support", unit_type: "팀", lifecycle: "active", display_order: 1, member_count: 2, direct_member_count: 2, leaders: [] },
];

const HARAM = {
  member_id: "2417",
  display_name: "유하람",
  memberships: [
    { organization_id: "finance", organization_name: "재무회계팀", kind: "primary" },
    { organization_id: "support", organization_name: "경영지원본부", kind: "concurrent" },
  ],
  positions: [],
  grade: "선임",
  jobs: [],
};

const SEYEON = {
  member_id: "2183",
  display_name: "김세연",
  memberships: [{ organization_id: "finance", organization_name: "재무회계팀", kind: "primary" }],
  positions: [{ position: "팀장", organization_name: "재무회계팀", kind: "primary" }],
  grade: "책임",
  jobs: [],
};

const DOHYUN = {
  member_id: "1001",
  display_name: "윤도현",
  memberships: [{ organization_id: "support", organization_name: "경영지원본부", kind: "primary" }],
  positions: [],
  grade: "이사",
  jobs: [],
};

const DIRECT_GRANT = {
  grant_id: "grant-direct",
  role_id: "role:approver",
  role_label: "보고 승인",
  capability_id: null,
  role_capability_version: 1,
  scope_kind: "unit",
  scope_ref: "finance",
  scope_name: "재무회계팀",
  include_descendants: true,
  origin_rule_id: null,
  granted_by: "1001",
  valid_from: "2026-01-01T00:00:00+00:00",
  valid_until: null,
};

const STANDARD_GRANT = { ...DIRECT_GRANT, grant_id: "grant-standard", role_label: "팀장", origin_rule_id: "standard:appointment" };

const revokedGrant = (index: number) => ({
  grant_id: `grant-revoked-${index}`,
  role_id: "role:approver",
  role_label: `옛 역할 ${index}`,
  scope_kind: "unit",
  scope_ref: "finance",
  scope_name: "재무회계팀",
  valid_from: "2025-03-02T00:00:00+00:00",
  revoked_at: "2026-01-14T00:00:00+00:00",
});

/** `GET /api/organization/members/{id}` — 여섯 축이 한 응답에 온다. 모양은 누구에게나 같다. */
function memberAxes(overrides: Record<string, unknown> = {}) {
  return {
    member_id: "2417",
    display_name: "유하람",
    employment_state: "active",
    employment_type: "regular",
    has_account: true,
    phone: null,
    birth_date: null,
    hierarchy_path: [
      { unit_id: "the-sc", name: "더에쓰씨", unit_type: "회사" },
      { unit_id: "support", name: "경영지원본부", unit_type: "본부" },
      { unit_id: "finance", name: "재무회계팀", unit_type: "팀" },
    ],
    memberships: [
      { unit_id: "finance", unit_name: "재무회계팀", kind: "primary", valid_from: "2024-01-01T00:00:00+00:00", valid_until: null },
      { unit_id: "support", unit_name: "경영지원본부", kind: "concurrent", valid_from: "2025-06-01T00:00:00+00:00", valid_until: null },
    ],
    appointments: [],
    grade: { id: "senior", name: "선임" },
    jobs: [],
    grants: [DIRECT_GRANT, STANDARD_GRANT],
    revoked_grants: [],
    ...overrides,
  };
}

let activitySeq = 0;

const activityEvent = (overrides: Record<string, unknown> = {}) => {
  const occurredAt = (overrides.occurred_at as string | undefined) ?? "2026-09-07T00:12:00+00:00";
  return {
    occurred_at: occurredAt,
    axis: "권한",
    event_kind: "access.grant_added",
    summary: "유하람 · 보고 승인 부여",
    reason: "승인자 보강",
    actor_id: "1001",
    actor_name: "윤도현",
    target_id: "2417",
    target_type: "member",
    // 서버는 행마다 (시각, id) 복합 커서를 실어 준다 — 「더 보기」는 이것을 그대로 되보낸다 (PR #2 F4).
    cursor: `${occurredAt}|${String(activitySeq++).padStart(4, "0")}`,
    ...overrides,
  };
};

type Options = {
  administers?: boolean;
  me?: string;
  ownGrants?: unknown[];
  detail?: Record<string, unknown>;
  detailStatus?: number;
  /** 축별 이력. 없는 축은 빈 배열로 답한다. */
  history?: Record<string, unknown[]>;
  historyStatus?: number;
  activity?: unknown[];
  activityStatus?: number;
  /** 조직마다 다른 변경 기록. 주면 `activity` 대신 이것을 쓴다. */
  activityByUnit?: Record<string, unknown[]>;
  /** 상세 응답을 사람마다 붙잡아 두는 문 — 늦게 도착하는 응답을 테스트가 만든다. */
  detailGate?: (memberId: string) => Promise<void> | undefined;
  /** 변경 기록 응답을 붙잡아 두는 문 — 요청 URL 을 보고 고른다. */
  activityGate?: (url: URL) => Promise<void> | undefined;
};

/** 손으로 열고 닫는 약속 하나 — 응답이 언제 도착할지를 테스트가 정한다. */
function gate() {
  let open = () => {};
  const promise = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { promise, open: () => open() };
}

/** 상세 응답의 이름은 물어본 사람의 것이어야 한다 — 아니면 늦은 응답과 새 응답을 구별할 수 없다. */
const NAMES: Record<string, string> = { "2417": "유하람", "2183": "김세연", "1001": "윤도현" };

function mockApi(options: Options = {}) {
  const administers = options.administers ?? true;
  const calls: Array<{ path: string; body?: unknown }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const raw = String(input);
    calls.push({ path: raw, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const url = new URL(raw, "http://localhost");
    const path = url.pathname;
    if (path === "/api/organization/me") {
      return jsonResponse({
        member_id: options.me ?? "1001",
        display_name: options.me === "2417" ? "유하람" : "윤도현",
        organizations: [{ id: "finance", name: "재무회계팀" }],
        roles: [],
        grants: options.ownGrants ?? [DIRECT_GRANT],
        capabilities: administers ? ["task.read", "organization.manage"] : ["task.read"],
      });
    }
    if (path === "/api/organization/tree") return jsonResponse(UNITS);
    if (path === "/api/organization/units/the-sc/members") return jsonResponse([HARAM, SEYEON, DOHYUN]);
    if (path === "/api/organization/units/finance/members") return jsonResponse([HARAM, SEYEON]);
    if (path === "/api/organization/units/support/members") return jsonResponse([DOHYUN]);
    if (path === "/api/organization/activity") {
      if (options.activityStatus && options.activityStatus !== 200) return jsonResponse({ detail: "자격이 없습니다" }, options.activityStatus);
      await options.activityGate?.(url);
      const perUnit = options.activityByUnit?.[url.searchParams.get("unit_id") ?? ""];
      const events = (perUnit ?? options.activity ?? []) as Array<Record<string, unknown>>;
      const cursor = url.searchParams.get("cursor");
      const limit = Number(url.searchParams.get("limit") ?? 50);
      // 서버는 cursor 보다 앞선 것을 limit 만큼 준다 — 그 규칙을 여기서도 지켜야 「더 보기」가 진짜로 검증된다.
      // 경계는 (시각, id) 복합 커서다. 시각만 적힌 옛 커서를 받으면 같은 시각의 행은 지나쳐 버린다 — 서버와
      // 같은 동작이고, 그래서 「더 보기」가 무엇을 되보내는지가 이 mock 에서 드러난다 (PR #2 F4).
      const page = cursor ? events.filter((event) => String(event.cursor ?? event.occurred_at) < cursor) : events;
      return jsonResponse(page.slice(0, limit));
    }
    if (path.endsWith("/history")) {
      if (options.historyStatus && options.historyStatus !== 200) {
        return jsonResponse({ detail: "이 구성원의 이력을 볼 수 있는 범위가 아닙니다" }, options.historyStatus);
      }
      return jsonResponse(options.history?.[url.searchParams.get("axis") ?? ""] ?? []);
    }
    if (path.startsWith("/api/organization/members/")) {
      if (options.detailStatus && options.detailStatus !== 200) return jsonResponse({ detail: "찾을 수 없습니다" }, options.detailStatus);
      const memberId = path.split("/")[4] ?? "2417";
      await options.detailGate?.(memberId);
      return jsonResponse(memberAxes({ member_id: memberId, display_name: NAMES[memberId] ?? "유하람", ...options.detail }));
    }
    if (path === "/api/access/roles") {
      return jsonResponse([{ role_id: "role:approver", label: "보고 승인", version: 1, template_key: null, customized: false, capabilities: [] }]);
    }
    if (path.endsWith("/revoke")) return jsonResponse({ grant_id: "grant-direct" });
    if (path === "/api/access/grants") return jsonResponse({ grant_id: "grant-new" });
    return jsonResponse({ detail: `unhandled ${raw}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

/** 목록이 도착할 때까지 기다렸다가 그 사람 행을 누른다. */
async function pickMember(name: string) {
  const row = await screen.findByRole("button", { name: new RegExp(name) });
  fireEvent.click(row);
  return screen.findByRole("region", { name: `${name} 상세` });
}

const changeLog = () => screen.getByRole("region", { name: "변경 기록" });

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("OrgPage — 조직 화면 v3", () => {
  it("관리자에게는 관리 배지가 있고 직원에게는 읽기 전용 배지만 있다", async () => {
    mockApi({ administers: true });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    expect(await screen.findByText("조직 관리 권한 있음")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "조직 tree" })).getByText("읽기 전용")).toBeTruthy();
  });

  it("직원에게는 변경 기록도 권한 변경도 없고, 남의 권한은 자격이 없다고 말한다", async () => {
    mockApi({ administers: false, detail: { grants: [], revoked_grants: [] }, me: "1001" });
    render(<OrgPage onError={vi.fn()} personaId="2183" />);

    const head = (await screen.findByRole("heading", { name: "조직" })).closest(".page-head") as HTMLElement;
    expect(within(head).getByText("읽기 전용")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "변경 기록" })).toBeNull();

    await pickMember("유하람");
    expect(await screen.findByText("관리 권한이 있는 사람에게만 보입니다")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "변경" })).toBeNull();
    // 볼 자격이 없으면 「이력」도 그리지 않는다 — 눌러 봐야 403 인 버튼을 두지 않는다.
    expect(screen.queryByRole("button", { name: "이력" })).toBeNull();
  });

  it("직원이 자기 자신을 고르면 그 응답에 자기 권한이 담겨 온다", async () => {
    mockApi({ administers: false, me: "2417" });
    render(<OrgPage onError={vi.fn()} personaId="2417" />);

    await pickMember("유하람");
    expect(await screen.findByText(/보고 승인 · 재무회계팀 이하/)).toBeTruthy();
    expect(screen.queryByText("관리 권한이 있는 사람에게만 보입니다")).toBeNull();
  });

  it("한 사람은 members/{id} 한 번으로 읽는다 — 목록 응답과 access 를 이어 붙이지 않는다", async () => {
    const calls = mockApi({ administers: true });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    const detail = await pickMember("유하람");

    await waitFor(() => expect(calls.some((call) => call.path === "/api/organization/members/2417")).toBe(true));
    expect(calls.some((call) => call.path.startsWith("/api/access/members/"))).toBe(false);
    // 계층은 응답이 준 길을 그대로 그린다 — 프론트가 트리에서 다시 계산하지 않는다.
    expect(within(detail).getByText("더에쓰씨 › 경영지원본부 › 재무회계팀")).toBeTruthy();
    expect(within(detail).getByText("재무회계팀 (주) · 경영지원본부 (겸직)")).toBeTruthy();
  });

  it("소속·직책 축에는 「변경」이 없고, 계층의 「이력」만 눌리지 않는다", async () => {
    mockApi({ administers: true });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    const detail = await pickMember("유하람");

    const histories = within(detail).getAllByRole("button", { name: "이력" });
    expect(histories.length).toBe(6);
    // 계층은 스스로의 이력을 갖지 않는다 — 소속이 지나온 길을 다시 그린 것이다.
    const disabled = histories.filter((button) => (button as HTMLButtonElement).disabled);
    expect(disabled.length).toBe(1);
    expect(disabled[0].getAttribute("title")).toBe("계층은 소속 축의 이력이 말합니다");
    expect(within(detail).getAllByRole("button", { name: "변경" }).length).toBe(1);
    expect(screen.queryByRole("button", { name: "행 메뉴" })).toBeNull();
  });

  it("계정 배지는 has_account 를 그대로 읽는다", async () => {
    mockApi({ administers: true, detail: { has_account: true } });
    const { unmount } = render(<OrgPage onError={vi.fn()} personaId="1001" />);
    let detail = await pickMember("유하람");
    expect(within(detail).getByText("로그인 계정 있음")).toBeTruthy();
    expect(within(detail).getByText("재직")).toBeTruthy();
    unmount();
    cleanup();

    mockApi({ administers: true, detail: { has_account: false } });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    detail = await pickMember("유하람");
    expect(within(detail).getByText("계정 없음")).toBeTruthy();
  });
});

describe("OrgPage — 축별 이력", () => {
  it("「이력」을 누르면 그 축의 이력을 팝오버로 편다", async () => {
    const calls = mockApi({
      administers: true,
      history: {
        membership: [
          { value: "재무회계팀", unit_name: "재무회계팀", kind: "primary", valid_from: "2024-01-01T00:00:00+00:00", valid_until: null, reason: "입사 배치", actor: null },
          { value: "제품기획팀", unit_name: "제품기획팀", kind: "primary", valid_from: "2022-03-01T00:00:00+00:00", valid_until: "2023-12-31T00:00:00+00:00", reason: null, actor: null },
        ],
      },
    });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    const detail = await pickMember("유하람");

    const rows = within(detail).getAllByRole("button", { name: "이력" });
    // 계층(disabled) 다음이 소속이다.
    fireEvent.click(rows[1]);
    const panel = await screen.findByRole("group", { name: "소속 이력" });

    await waitFor(() => expect(within(panel).getByText("재무회계팀")).toBeTruthy());
    expect(calls.some((call) => call.path === "/api/organization/members/2417/history?axis=membership")).toBe(true);
    // 아직 끝나지 않은 기간은 「현재」로 닫는다.
    expect(within(panel).getByText("2024/01/01 – 현재")).toBeTruthy();
    expect(within(panel).getByText("2022/03/01 – 2023/12/31")).toBeTruthy();
    expect(within(panel).getByText("입사 배치")).toBeTruthy();
    expect(within(panel).getByText("사유 없음")).toBeTruthy();
  });

  it("남아 있는 이력이 없으면 그렇다고 말한다 — 직무 축은 이 데이터에서 늘 그렇다", async () => {
    mockApi({ administers: true, history: {} });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    const detail = await pickMember("유하람");

    fireEvent.click(within(detail).getAllByRole("button", { name: "이력" })[4]);
    const panel = await screen.findByRole("group", { name: "직무 이력" });
    expect(await within(panel).findByText("남아 있는 이력이 없습니다")).toBeTruthy();
  });

  it("서버가 403 을 주면 팝오버 안에서 말하고 Toast 로도 알린다", async () => {
    mockApi({ administers: true, historyStatus: 403 });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    const detail = await pickMember("유하람");

    fireEvent.click(within(detail).getAllByRole("button", { name: "이력" })[1]);
    const panel = await screen.findByRole("group", { name: "소속 이력" });
    expect(await within(panel).findByText("이 구성원의 이력을 볼 수 있는 범위가 아닙니다")).toBeTruthy();
    // 팝오버 안에서 한 번, 화면 전체가 한 번 — 자격이 없다는 답은 작은 패널만 알고 있으면 안 된다.
    await waitFor(() => expect(screen.getAllByText("이 구성원의 이력을 볼 수 있는 범위가 아닙니다").length).toBeGreaterThan(1));
  });
});

describe("OrgPage — 회수된 권한", () => {
  it("회수분이 없으면 절 자체를 그리지 않는다", async () => {
    mockApi({ administers: true, detail: { revoked_grants: [] } });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    const detail = await pickMember("유하람");
    expect(within(detail).queryByText("회수된 권한")).toBeNull();
  });

  it("1건이면 그 한 줄만 그리고 「더 보기」는 없다", async () => {
    mockApi({ administers: true, detail: { revoked_grants: [revokedGrant(1)] } });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    const detail = await pickMember("유하람");

    expect(within(detail).getByText("회수된 권한")).toBeTruthy();
    expect(within(detail).getByText("옛 역할 1 · 재무회계팀")).toBeTruthy();
    // 회수는 지우는 것이 아니다 — 언제부터 언제까지였는지가 남는다.
    expect(within(detail).getByText("2025/03/02 – 2026/01/14")).toBeTruthy();
    expect(within(detail).queryByRole("button", { name: /더 보기/ })).toBeNull();
  });

  it("4건이면 셋만 펴고 나머지는 「1건 더 보기」로 접어 둔다", async () => {
    mockApi({ administers: true, detail: { revoked_grants: [1, 2, 3, 4].map(revokedGrant) } });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    const detail = await pickMember("유하람");

    expect(within(detail).queryByText("옛 역할 4 · 재무회계팀")).toBeNull();
    const more = within(detail).getByRole("button", { name: "1건 더 보기" });
    fireEvent.click(more);
    expect(within(detail).getByText("옛 역할 4 · 재무회계팀")).toBeTruthy();
    fireEvent.click(within(detail).getByRole("button", { name: "접기" }));
    expect(within(detail).queryByText("옛 역할 4 · 재무회계팀")).toBeNull();
  });
});

describe("OrgPage — 변경 기록", () => {
  it("0건이면 표 대신 비어 있다고 말한다", async () => {
    mockApi({ administers: true, activity: [] });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    await screen.findByRole("region", { name: "재무회계팀 구성원" });
    await waitFor(() => expect(within(changeLog()).getByText("이 조직에서 남은 변경이 없습니다")).toBeTruthy());
    expect(changeLog().querySelector("table")).toBeNull();
  });

  it("n건이면 핸드오프의 표로 그리고 축마다 배지를 붙인다", async () => {
    mockApi({
      administers: true,
      activity: [
        activityEvent(),
        activityEvent({ axis: "소속", event_kind: "organization.membership_added", occurred_at: "2026-08-27T01:05:00+00:00", summary: "유하람 · 겸직 추가" }),
      ],
    });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    await waitFor(() => expect(changeLog().querySelector("table")).toBeTruthy());

    const rows = within(changeLog()).getAllByRole("row").slice(1);
    expect(rows.length).toBe(2);
    expect(within(rows[0]).getByText("유하람 · 보고 승인 부여")).toBeTruthy();
    expect(within(rows[0]).getByText("승인자 보강")).toBeTruthy();
    expect(within(rows[0]).getByText("윤도현")).toBeTruthy();
    // 실제 접근을 바꾸는 축은 권한 하나뿐이라 그것만 ai 배지다.
    expect(within(rows[0]).getByText("권한").className).toContain("badge ai");
    expect(within(rows[1]).getByText("소속").className).toContain("badge neutral");
  });

  it("한 쪽이 가득 차면 「더 보기」로 다음 쪽을 이어 붙인다", async () => {
    const events = Array.from({ length: 51 }, (_, index) =>
      activityEvent({ occurred_at: `2026-09-${String(51 - index).padStart(2, "0")}T00:00:00+00:00`, summary: `사건 ${index}` }),
    );
    const calls = mockApi({ administers: true, activity: events });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    await waitFor(() => expect(changeLog().querySelector("table")).toBeTruthy());

    expect(within(changeLog()).getAllByRole("row").length - 1).toBe(50);
    fireEvent.click(within(changeLog()).getByRole("button", { name: "더 보기" }));
    await waitFor(() => expect(within(changeLog()).getAllByRole("row").length - 1).toBe(51));
    expect(calls.some((call) => call.path.includes("cursor="))).toBe(true);
  });

  it("「더 보기」는 마지막 행의 cursor 를 그대로 되보낸다 — 같은 시각의 나머지를 건너뛰지 않는다", async () => {
    const moment = "2026-09-07T00:12:00+00:00";
    // 한 쪽(50)이 같은 시각에서 끝나고, 그 시각의 나머지 한 건이 다음 쪽에 남는다.
    const events = [
      ...Array.from({ length: 49 }, (_, index) =>
        activityEvent({ occurred_at: new Date(Date.UTC(2026, 8, 30, 12) - index * 3_600_000).toISOString(), summary: `사건 ${index}` }),
      ),
      activityEvent({ cursor: `${moment}|b`, occurred_at: moment, summary: "같은 시각 앞" }),
      activityEvent({ cursor: `${moment}|a`, occurred_at: moment, summary: "같은 시각 뒤" }),
    ];
    const calls = mockApi({ administers: true, activity: events });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    await waitFor(() => expect(within(changeLog()).getAllByRole("row").length - 1).toBe(50));

    fireEvent.click(within(changeLog()).getByRole("button", { name: "더 보기" }));
    await waitFor(() => expect(within(changeLog()).getByText("같은 시각 뒤")).toBeTruthy());

    const more = calls.find((call) => call.path.includes("cursor="));
    // 시각만 보내면 같은 시각의 나머지가 통째로 빠진다 — 행이 준 자리표를 손대지 않고 그대로 되보낸다.
    expect(new URL(more?.path ?? "", "http://localhost").searchParams.get("cursor")).toBe(`${moment}|b`);
  });

  it("고른 조직이 바뀌면 그 조직으로 다시 묻는다", async () => {
    const calls = mockApi({ administers: true, activity: [activityEvent()] });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    await waitFor(() => expect(calls.some((call) => call.path.includes("unit_id=finance"))).toBe(true));

    const tree = screen.getByRole("region", { name: "조직 tree" });
    fireEvent.click(within(tree).getByRole("button", { name: /경영지원본부 본부/ }));
    await waitFor(() => expect(calls.some((call) => call.path.includes("unit_id=support"))).toBe(true));
  });
});

describe("OrgPage — 권한 변경", () => {
  it("tree 의 펼침과 선택은 서로 다른 일이다", async () => {
    mockApi({ administers: true });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);

    const tree = await screen.findByRole("region", { name: "조직 tree" });
    expect(await within(tree).findByRole("button", { name: /재무회계팀/ })).toBeTruthy();
    fireEvent.click(within(tree).getByRole("button", { name: "경영지원본부 접기" }));
    expect(within(tree).queryByRole("button", { name: /재무회계팀/ })).toBeNull();
    expect(screen.getByRole("region", { name: "재무회계팀 구성원" })).toBeTruthy();

    fireEvent.click(within(tree).getByRole("button", { name: /경영지원본부 본부/ }));
    expect(await screen.findByRole("region", { name: "경영지원본부 구성원" })).toBeTruthy();
    expect(within(tree).getByRole("button", { name: "경영지원본부 펼치기" })).toBeTruthy();
  });

  it("회수는 Drawer 를 닫은 뒤에 한 번 더 묻고, 성공하면 상세와 변경 기록을 다시 읽는다", async () => {
    const calls = mockApi({ administers: true, activity: [] });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    const detail = await pickMember("유하람");

    fireEvent.click(within(detail).getByRole("button", { name: "변경" }));
    const drawer = await screen.findByRole("dialog", { name: "권한 변경" });
    // 보직이 데려온 권한은 보직과 함께 끝난다 — 여기서 따로 회수하지 않는다.
    expect(within(drawer).getAllByRole("button", { name: "회수" }).length).toBe(1);

    fireEvent.change(within(drawer).getByLabelText("사유"), { target: { value: "조직 개편" } });
    fireEvent.click(within(drawer).getByRole("button", { name: "회수" }));
    expect(screen.queryByRole("dialog", { name: "권한 변경" })).toBeNull();

    const confirm = await screen.findByRole("alertdialog", { name: "이 권한을 회수할까요?" });
    fireEvent.click(within(confirm).getByRole("button", { name: "권한 회수" }));
    await waitFor(() => expect(calls.some((call) => call.path.endsWith("/revoke"))).toBe(true));
    expect(calls.find((call) => call.path.endsWith("/revoke"))?.body).toEqual({ reason: "조직 개편" });
    expect(await screen.findByText("권한을 회수했습니다")).toBeTruthy();
  });

  it("권한을 부여하면 Drawer 를 닫고 결과를 Toast 로 말한다", async () => {
    const calls = mockApi({ administers: true, activity: [] });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    const detail = await pickMember("유하람");

    fireEvent.click(within(detail).getByRole("button", { name: "변경" }));
    const drawer = await screen.findByRole("dialog", { name: "권한 변경" });
    fireEvent.click(within(drawer).getByLabelText("역할"));
    // 목록은 포털로 body 에 선다 (DS-18) — 드로어 안이 아니라 화면에서 찾는다
    fireEvent.click(screen.getByRole("option", { name: "보고 승인" }));
    fireEvent.click(within(drawer).getByLabelText("범위"));
    fireEvent.click(screen.getByRole("option", { name: "재무회계팀" }));
    fireEvent.change(within(drawer).getByLabelText("사유"), { target: { value: "승인자 보강" } });
    fireEvent.click(within(drawer).getByRole("button", { name: "권한 부여" }));

    await waitFor(() => expect(calls.some((call) => call.path === "/api/access/grants")).toBe(true));
    expect(calls.find((call) => call.path === "/api/access/grants")?.body).toEqual({
      member_id: "2417",
      role_id: "role:approver",
      scope_kind: "unit",
      scope_ref: "finance",
      include_descendants: true,
      reason: "승인자 보강",
    });
    expect(await screen.findByText("권한을 부여했습니다")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "권한 변경" })).toBeNull();
  });
});

/**
 * 응답은 보낸 순서대로 오지 않는다. 늦게 도착한 앞선 요청의 답이 지금 화면을 덮으면, 보이는 것과 바뀌는 것이
 * 갈라진다 — 권한 화면에서 그 갈라짐은 **남의 권한을 바꾸는 일**이다.
 */
describe("OrgPage — 늦게 도착한 응답", () => {
  it("A 의 상세가 늦게 와도 B 를 덮지 않고, 부여 대상은 화면에 보이는 사람이다", async () => {
    const haram = gate();
    const calls = mockApi({ administers: true, activity: [], detailGate: (id) => (id === "2417" ? haram.promise : undefined) });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);

    // A(유하람) 를 고르고 응답을 붙잡아 둔 채 B(김세연) 로 옮긴다. B 의 응답만 먼저 도착한다.
    fireEvent.click(await screen.findByRole("button", { name: /유하람/ }));
    fireEvent.click(screen.getByRole("button", { name: /김세연/ }));
    const detail = await screen.findByRole("region", { name: "김세연 상세" });

    await act(async () => {
      haram.open();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.queryByRole("region", { name: "유하람 상세" })).toBeNull();

    fireEvent.click(within(detail).getByRole("button", { name: "변경" }));
    const drawer = await screen.findByRole("dialog", { name: "권한 변경" });
    expect(within(drawer).getByText(/김세연 · 2183/)).toBeTruthy();
    fireEvent.click(within(drawer).getByLabelText("역할"));
    // 목록은 포털로 body 에 선다 (DS-18) — 드로어 안이 아니라 화면에서 찾는다
    fireEvent.click(screen.getByRole("option", { name: "보고 승인" }));
    fireEvent.click(within(drawer).getByLabelText("범위"));
    fireEvent.click(screen.getByRole("option", { name: "재무회계팀" }));
    fireEvent.change(within(drawer).getByLabelText("사유"), { target: { value: "승인자 보강" } });
    fireEvent.click(within(drawer).getByRole("button", { name: "권한 부여" }));

    await waitFor(() => expect(calls.some((call) => call.path === "/api/access/grants")).toBe(true));
    // 화면이 김세연을 보여 주고 있었으니 바뀌는 사람도 김세연이어야 한다.
    expect((calls.find((call) => call.path === "/api/access/grants")?.body as { member_id: string }).member_id).toBe("2183");
  });

  it("조직을 바꾸면 이전 조직의 「더 보기」 응답은 새 표에 붙지 않는다", async () => {
    const financeEvents = Array.from({ length: 51 }, (_, index) =>
      activityEvent({ occurred_at: new Date(Date.UTC(2026, 8, 30, 12) - index * 3_600_000).toISOString(), summary: `재무 ${index}` }),
    );
    const more = gate();
    mockApi({
      administers: true,
      activityByUnit: {
        finance: financeEvents,
        support: [activityEvent({ occurred_at: "2026-08-01T00:00:00.000Z", summary: "경영지원 사건" })],
      },
      activityGate: (url) => (url.searchParams.get("cursor") ? more.promise : undefined),
    });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    await waitFor(() => expect(within(changeLog()).getAllByRole("row").length - 1).toBe(50));

    // 다음 쪽을 부른 채 다른 조직으로 옮긴다.
    fireEvent.click(within(changeLog()).getByRole("button", { name: "더 보기" }));
    const tree = screen.getByRole("region", { name: "조직 tree" });
    fireEvent.click(within(tree).getByRole("button", { name: /경영지원본부 본부/ }));
    await waitFor(() => expect(within(changeLog()).getByText("경영지원 사건")).toBeTruthy());

    await act(async () => {
      more.open();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(within(changeLog()).getAllByRole("row").length - 1).toBe(1);
    expect(within(changeLog()).queryByText("재무 50")).toBeNull();
  });

  it("이전 조직의 첫 쪽이 늦게 와도 새 조직의 표를 덮지 않는다", async () => {
    const finance = gate();
    mockApi({
      administers: true,
      activityByUnit: {
        finance: [activityEvent({ summary: "재무 사건" })],
        support: [activityEvent({ occurred_at: "2026-08-01T00:00:00.000Z", summary: "경영지원 사건" })],
      },
      activityGate: (url) => (url.searchParams.get("unit_id") === "finance" ? finance.promise : undefined),
    });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    const tree = await screen.findByRole("region", { name: "조직 tree" });
    fireEvent.click(await within(tree).findByRole("button", { name: /경영지원본부 본부/ }));
    await waitFor(() => expect(within(changeLog()).getByText("경영지원 사건")).toBeTruthy());

    await act(async () => {
      finance.open();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(within(changeLog()).getByText("경영지원 사건")).toBeTruthy();
    expect(within(changeLog()).queryByText("재무 사건")).toBeNull();
  });
});

/** Esc 는 열려 있는 겹 중 **맨 위 하나**만 닫는다 — 고르기를 그만두려다 쓰던 것을 잃지 않는다. */
describe("OrgPage — 겹친 오버레이의 Esc", () => {
  it("Drawer 안 Select 를 Esc 로 닫아도 Drawer 와 쓰던 사유는 그대로다", async () => {
    mockApi({ administers: true, activity: [] });
    render(<OrgPage onError={vi.fn()} personaId="1001" />);
    const detail = await pickMember("유하람");

    fireEvent.click(within(detail).getByRole("button", { name: "변경" }));
    const drawer = await screen.findByRole("dialog", { name: "권한 변경" });
    fireEvent.change(within(drawer).getByLabelText("사유"), { target: { value: "승인자 보강" } });
    fireEvent.click(within(drawer).getByLabelText("역할"));
    expect(await screen.findByRole("option", { name: "보고 승인" })).toBeTruthy();

    fireEvent.keyDown(document, { key: "Escape" });

    // 목록만 닫힌다. Drawer 도 사유도 그 자리에 있다.
    expect(screen.queryByRole("option", { name: "보고 승인" })).toBeNull();
    const stillOpen = screen.getByRole("dialog", { name: "권한 변경" });
    expect((within(stillOpen).getByLabelText("사유") as HTMLTextAreaElement).value).toBe("승인자 보강");
    // 닫은 눈은 열었던 트리거로 돌아온다.
    expect(document.activeElement).toBe(within(stillOpen).getByLabelText("역할"));

    // 한 번 더 누르면 그때 Drawer 가 닫힌다 — 한 번에 한 겹씩.
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "권한 변경" })).toBeNull();
  });
});
