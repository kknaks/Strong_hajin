import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { useBrowserOperationGuard } from './browserOperationGuard';

const jsonResponse = (body: unknown) =>
  new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
  });


type FetchImpl = (input: RequestInfo | URL, init?: RequestInit) => Promise<unknown>;

/** Wraps a persona-header based fetch mock with a login session so tests exercise the real auth gate. */
function withSession(fetchMock: FetchImpl, initialPersona = "mina"): (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> {
  let current: string | null = initialPersona;
  const withPersona = (init?: RequestInit): RequestInit => ({
    ...init,
    headers: { ...(init?.headers as Record<string, string> | undefined), ...(current ? { "X-Demo-Persona": current } : {}) },
  });
  return async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/providers") return jsonResponse({ local: true, oidc: false });
    if (path === "/api/auth/login") {
      // The demo signs in with an ordinary address; the member it belongs to is what the session then carries.
      current = (JSON.parse(String(init?.body)) as { email: string }).email.split("@")[0];
      return (await fetchMock("/api/organization/me", withPersona({ ...init, method: "GET", body: undefined }))) as Response;
    }
    if (path === "/api/auth/logout") {
      current = null;
      return new Response(null, { status: 204 });
    }
    if (path === "/api/action-items") {
      // The unified judgement ledger is on every work surface; tests that care about it mock it explicitly.
      const response = (await fetchMock(path, withPersona(init))) as Response;
      return response.ok ? response : jsonResponse([]);
    }
    if (path === "/api/auth/me") {
      if (!current) return new Response(JSON.stringify({ detail: "로그인이 필요합니다." }), { status: 401 });
      return (await fetchMock("/api/organization/me", withPersona(init))) as Response;
    }
    return (await fetchMock(path, withPersona(init))) as Response;
  };
}

/** One judgement in the unified ledger, as the server projects it. */
function judgement(overrides: Record<string, unknown>) {
  return {
    action_item_id: "action-item-1",
    kind: "work_request.acceptance",
    status: "awaiting_review",
    subject: "판단할 일",
    operation_label: "업무 요청",
    current_question: "이 업무 요청을 수락할지 결정하세요",
    preview: [],
    allowed_commands: [{ id: "accept", label: "수락", tone: "primary", requires_reason: false }],
    submission_version: 1,
    waiting_on: { member_id: "mina", display_name: "민아 (구성원)" },
    resource: { type: "work_request", id: "request-1" },
    expected_version: 1,
    ...overrides,
  };
}

function judgementDetail(overrides: Record<string, unknown>) {
  const item = judgement(overrides);
  return {
    ...item,
    rounds: [
      {
        submission_id: "submission-1",
        submission_version: 1,
        submitted_by: "jiho",
        submitted_at: "2026-09-03T00:00:00Z",
        content_hash: "hash-1",
        snapshot: { title: item.subject },
        diff: null,
        decisions: [],
      },
    ],
  };
}

function seoulTodayForTest(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Seoul" });
}

/** 오늘이 아닌, 오늘이 속한 달 격자 안의 하루 — 달력에서 한 번에 누를 수 있는 자리다. */
function otherReportDate(): string {
  const today = seoulTodayForTest();
  return `${today.slice(0, 7)}-${today.endsWith("-01") ? "02" : "01"}`;
}

async function switchAccount(personaId: string) {
  fireEvent.click(screen.getByRole("button", { name: "로그아웃" }));
  fireEvent.change(await screen.findByLabelText("이메일"), { target: { value: `${personaId}@scax.example` } });
  fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "scax-demo-1234" } });
  fireEvent.click(screen.getByRole("button", { name: "로그인" }));
  await screen.findByRole("navigation", { name: "제품 탐색" });
}

describe("product surfaces", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('keeps the current surface and login while a browser upload is pending', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === '/api/organization/me') return jsonResponse({ member_id: 'mina', display_name: '민아', organizations: [], roles: [], capabilities: ['task.read', 'task.self_manage', 'action.read'] });
      return jsonResponse([]);
    });
    vi.stubGlobal('fetch', withSession(fetchMock));
    function PendingUpload() { useBrowserOperationGuard(true); return null; }
    const { rerender } = render(<><App /><PendingUpload /></>);
    const navigation = await screen.findByRole('navigation', { name: '제품 탐색' });
    fireEvent.click(within(navigation).getByRole('button', { name: '내 업무' }));
    expect(await screen.findByText('파일 업로드나 녹음이 끝난 뒤 이동할 수 있습니다.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '로그아웃' }));
    expect(screen.queryByLabelText('이메일')).toBeNull();
    rerender(<><App /></>);
    fireEvent.click(screen.getByRole('button', { name: '로그아웃' }));
    expect(await screen.findByLabelText('이메일')).toBeTruthy();
  });

  it("shows a pending manager assignment in the decision panel and moves it into My Work on accept", async () => {
    let assignmentStatus: "pending" | "active" = "pending";
    const assignedTask = {
      task_id: "task-9",
      title: "분기 보고 정리",
      state: "open",
      version: 1,
      block_reason: null,
      description: "지난 분기 수치",
      due_date: "2026-09-30",
      origin_kind: "assignment",
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/organization/members") return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }, { id: "jiho", display_name: "지호 (팀장)" }]);
      if (path === "/api/organization/me") {
        return jsonResponse({ member_id: "mina", display_name: "민아 (구성원)", organizations: [], capabilities: ["task.read", "task.self_manage"] });
      }
      if (path === "/api/my-work") {
        return jsonResponse(assignmentStatus === "active" ? [{ ...assignedTask, assignment: { assignment_id: "as-1", kind: "direct", status: "active", assigned_by: "jiho", accepted_at: "2026-09-04T00:00:00Z" } }] : []);
      }
      if (path === "/api/tasks?include_closed=true") return jsonResponse([]);
      if (path === "/api/work-requests") return jsonResponse([]);
      if (path === "/api/action-items") {
        return jsonResponse(
          assignmentStatus === "pending"
            ? [judgement({ action_item_id: "as-1", kind: "task.assignment", subject: "분기 보고 정리", operation_label: "업무 배정", current_question: "이 업무 배정을 수락할지 결정하세요", resource: { type: "task", id: "task-9" } })]
            : [],
        );
      }
      if (path === "/api/action-items/as-1") {
        return jsonResponse(judgementDetail({ action_item_id: "as-1", kind: "task.assignment", subject: "분기 보고 정리", operation_label: "업무 배정", resource: { type: "task", id: "task-9" } }));
      }
      if (path === "/api/action-items/as-1/commands/accept" && init?.method === "POST") {
        assignmentStatus = "active";
        return jsonResponse(judgement({ action_item_id: "as-1", kind: "task.assignment", status: "resolved", allowed_commands: [] }));
      }
      if (path.startsWith("/api/daily-reports/status")) return jsonResponse({ report_date: seoulTodayForTest(), status: "not_started", report_id: null });
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    fireEvent.click(within(await screen.findByRole("navigation", { name: "제품 탐색" })).getByRole("button", { name: "내 업무" }));
    const panel = (await screen.findByText("판단이 필요한 업무")).closest(".decision-section") as HTMLElement;
    expect(await within(panel).findByText("분기 보고 정리")).toBeTruthy();
    expect(within(panel).getByText("업무 배정")).toBeTruthy();
    // Not in My Work before acceptance.
    expect(screen.queryAllByRole("row", { name: /분기 보고 정리/ })).toHaveLength(0);

    // The same drawer and the same command path as every other judgement.
    fireEvent.click(within(panel).getByRole("button", { name: "판단하기" }));
    const drawer = await screen.findByRole("dialog", { name: "판단 상세" });
    fireEvent.click(await within(drawer).findByRole("button", { name: "수락" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/action-items/as-1/commands/accept", expect.objectContaining({ method: "POST" })));
    await waitFor(() => expect(within(panel).queryByText("분기 보고 정리")).toBeNull());
    expect(await screen.findByRole("row", { name: /분기 보고 정리/ })).toBeTruthy();
  });

  it("shows an authorized direct task on Today and advances it from My Work", async () => {
    let taskState = "open";
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);

      if (path === "/api/organization/members") {
        return jsonResponse([
          { id: "mina", display_name: "민아 (구성원)" },
          { id: "demo-admin", display_name: "데모 관리자" },
        ]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: "mina",
          display_name: "민아 (구성원)",
          organizations: [],
          capabilities: ["daily_report.generate", "task.read", "task.self_manage", "work_request.decide"],
        });
      }

      if (path === "/api/my-work") {
        return jsonResponse([
          {
            task_id: "task-1",
            title: "고객 피드백 정리",
            state: taskState,
            version: 1,
            block_reason: null,
          },
        ]);
      }

      if (path === "/api/work-request-assignee-candidates") {
        return jsonResponse([{ id: "jiho", display_name: "지호 (팀장)" }]);
      }
      if (path === "/api/action-items") {
        return jsonResponse([judgement({ action_item_id: "request-1", subject: "오늘 확인할 업무 요청", resource: { type: "work_request", id: "request-1" } })]);
      }
      if (path.startsWith("/api/daily-reports/status")) {
        return jsonResponse({
          report_date: "2026-09-03",
          status: "draft",
          report_id: "report-1",
        });
      }

      if (path === "/api/tasks/task-1/start" && init?.method === "POST") {
        taskState = "in_progress";
        return jsonResponse({ task_id: "task-1", state: taskState });
      }

      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);

    expect(await screen.findByText("고객 피드백 정리")).toBeTruthy();
    expect(await screen.findByText("오늘 확인할 업무 요청")).toBeTruthy();
    expect(await screen.findByText("초안을 편집하거나 제출할 수 있습니다.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "일일보고 작성" })).toBeTruthy();

    const navigation = within(screen.getByRole("navigation", { name: "제품 탐색" }));
    fireEvent.click(navigation.getByRole("button", { name: "내 업무" }));
    expect(await screen.findByRole("button", { name: "시작" })).toBeTruthy();
    expect(screen.queryByText("수락된 배정")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "시작" }));

    await waitFor(() => {
      expect(screen.getByText("진행 중")).toBeTruthy();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/tasks/task-1/start",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows assigned work read-only when task.self_manage is not granted", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/organization/members") {
        return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: "mina",
          display_name: "민아 (구성원)",
          organizations: [],
          capabilities: ["task.read"],
        });
      }
      if (path === "/api/my-work") {
        return jsonResponse([
          {
            task_id: "task-1",
            title: "읽기 전용 업무",
            state: "open",
            version: 1,
            block_reason: null,
          },
        ]);
      }
      if (path === "/api/actions" || path === "/api/action-inbox") return jsonResponse([]);
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(screen.getByRole("navigation", { name: "제품 탐색" })).getByRole("button", { name: "내 업무" }));

    expect(await screen.findByText("읽기 전용 업무")).toBeTruthy();
    expect(screen.queryByLabelText("업무 제목")).toBeNull();
    expect(screen.queryByRole("button", { name: "업무 추가" })).toBeNull();
    expect(screen.queryByRole("button", { name: "시작" })).toBeNull();
  });

  it("returns to Today before a switched persona can load a forbidden report surface", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const personaId = new Headers(init?.headers).get("X-Demo-Persona");
      if (path === "/api/organization/members") {
        return jsonResponse([
          { id: "mina", display_name: "민아 (구성원)" },
          { id: "jiho", display_name: "지호 (팀장)" },
        ]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: personaId,
          display_name: personaId === "jiho" ? "지호 (팀장)" : "민아 (구성원)",
          organizations: [],
          capabilities: personaId === "mina" ? ["daily_report.generate"] : [],
        });
      }
      if (path === "/api/my-work" || path === "/api/action-inbox" || path === "/api/actions") {
        return jsonResponse([]);
      }
      if (path.startsWith("/api/daily-reports/status")) {
        return jsonResponse({ report_date: "2026-09-03", status: "not_started", report_id: null });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(screen.getByRole("navigation", { name: "제품 탐색" })).getByRole("button", { name: "보고" }));
    expect(await screen.findByRole("heading", { name: "개인 일일보고" })).toBeTruthy();

    await switchAccount("jiho");
    expect(await screen.findByText(/반갑습니다 지호님!/)).toBeTruthy();
    await waitFor(() => {
      expect(within(screen.getByRole("navigation", { name: "제품 탐색" })).queryByRole("button", { name: "보고" })).toBeNull();
    });
    expect(
      fetchMock.mock.calls.filter(([path, init]) => {
        return (
          String(path).startsWith("/api/daily-reports/") &&
          new Headers(init?.headers).get("X-Demo-Persona") === "jiho"
        );
      }),
    ).toHaveLength(0);
  });

  it("creates a work request through the UI and projects it only after the assignee accepts", async () => {
    let request: {
      request_id: string;
      title: string;
      state: "pending" | "accepted";
      version: number;
      task_id: string | null;
      assignment_state: string | null;
      conditions: null;
    } | null = null;

    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const headers = new Headers(init?.headers);
      const personaId = headers.get("X-Demo-Persona");

      if (path === "/api/organization/members") {
        return jsonResponse([
          { id: "mina", display_name: "민아 (구성원)" },
          { id: "jiho", display_name: "지호 (팀장)" },
        ]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: personaId,
          display_name: personaId === "jiho" ? "지호 (팀장)" : "민아 (구성원)",
          organizations: [],
          capabilities:
            personaId === "mina"
              ? ["daily_report.generate", "work_request.create"]
              : ["work_request.decide"],
        });
      }
      if (path === "/api/my-work") {
        return jsonResponse(
          personaId === "jiho" && request?.state === "accepted"
            ? [{ task_id: "task-1", title: request.title, state: "open", version: 1, block_reason: null }]
            : [],
        );
      }
      if (path === "/api/work-request-assignee-candidates") {
        return jsonResponse([{ id: "jiho", display_name: "지호 (팀장)" }]);
      }
      if (path === "/api/work-requests" && init?.method === "POST") {
        request = {
          request_id: "request-1",
          title: JSON.parse(String(init.body)).title,
          state: "pending",
          version: 1,
          task_id: null,
          assignment_state: null,
          conditions: null,
        };
        return jsonResponse(request);
      }
      if (path === "/api/action-items") {
        return jsonResponse(
          personaId === "jiho" && request?.state === "pending"
            ? [judgement({ action_item_id: "request-1", subject: request.title, resource: { type: "work_request", id: "request-1" }, waiting_on: { member_id: "jiho", display_name: "지호 (팀장)" } })]
            : [],
        );
      }
      if (path === "/api/action-items/request-1") {
        return jsonResponse(judgementDetail({ action_item_id: "request-1", subject: request?.title ?? "", resource: { type: "work_request", id: "request-1" } }));
      }
      if (path === "/api/action-items/request-1/commands/accept" && init?.method === "POST" && request) {
        request = { ...request, state: "accepted", version: 2, task_id: "task-1", assignment_state: "active" };
        return jsonResponse(judgement({ action_item_id: "request-1", status: "resolved", allowed_commands: [] }));
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(screen.getByRole("navigation", { name: "제품 탐색" })).getByRole("button", { name: "내 업무" }));
    fireEvent.click(await screen.findByRole("button", { name: "새 업무 추가" }));
    // 이 사람이 만들 수 있는 것은 요청 하나뿐이라 「업무/요청」 토글이 서지 않는다 (D10)
    const createDrawer = await screen.findByRole("dialog", { name: "업무 요청" });
    expect(within(createDrawer).queryByRole("tab")).toBeNull();
    await screen.findByLabelText("담당 후보");
    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "UI로 만든 업무 요청" } });
    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/work-requests",
        expect.objectContaining({ method: "POST" }),
      );
    });

    await switchAccount("jiho");
    await waitFor(() => {
      expect(within(screen.getByRole("navigation", { name: "제품 탐색" })).queryByRole("button", { name: "보고" })).toBeNull();
    });
    fireEvent.click(within(screen.getByRole("navigation", { name: "제품 탐색" })).getByRole("button", { name: "오늘" }));
    expect(await screen.findByText(/반갑습니다 지호님!/)).toBeTruthy();
    expect(screen.queryByText("보고 리마인드")).toBeNull();
    expect(screen.queryByRole("button", { name: "일일보고 작성" })).toBeNull();
    expect(
      fetchMock.mock.calls.filter(([path, init]) => {
        return (
          String(path).startsWith("/api/daily-reports/status") &&
          new Headers(init?.headers).get("X-Demo-Persona") === "jiho"
        );
      }),
    ).toHaveLength(0);
    expect(
      fetchMock.mock.calls.filter(([path, init]) => {
        return path === "/api/actions" && new Headers(init?.headers).get("X-Demo-Persona") === "jiho";
      }),
    ).toHaveLength(0);
    expect(
      fetchMock.mock.calls.filter(([path, init]) => {
        return (
          path === "/api/work-request-assignee-candidates" &&
          new Headers(init?.headers).get("X-Demo-Persona") === "jiho"
        );
      }),
    ).toHaveLength(0);
    expect(await screen.findByText("UI로 만든 업무 요청")).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "판단하기" }));
    const acceptDrawer = await screen.findByRole("dialog", { name: "판단 상세" });
    fireEvent.click(await within(acceptDrawer).findByRole("button", { name: "수락" }));

    fireEvent.click(within(screen.getByRole("navigation", { name: "제품 탐색" })).getByRole("button", { name: "내 업무" }));
    expect(await screen.findByText("UI로 만든 업무 요청")).toBeTruthy();
  });

  it("decides an AX proposal through the same judgement drawer and command path as every other kind", async () => {
    let decided: string | null = null;
    const proposal = {
      action_item_id: "action-1",
      kind: "ax.task.create_self",
      subject: "AX가 만든 업무",
      operation_label: "업무 생성",
      current_question: "AX가 준비한 변경을 승인할지 결정하세요",
      resource: { type: "action", id: "action-1" },
      expected_version: 4,
      preview: [{ id: "assignee", label: "담당", value: "민아 (구성원)", kind: "person" }],
      allowed_commands: [
        { id: "approve", label: "승인", tone: "primary", requires_reason: false },
        { id: "reject", label: "거절", tone: "neutral", requires_reason: false },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/organization/members") return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: "mina",
          display_name: "민아 (구성원)",
          organizations: [],
          capabilities: ["action.read", "action.decide", "task.read", "daily_report.generate"],
        });
      }
      if (path === "/api/my-work") return jsonResponse([]);
      if (path === "/api/tasks?include_closed=true") return jsonResponse([]);
      if (path === "/api/work-requests") return jsonResponse([]);
      if (path === "/api/work-request-assignee-candidates") return jsonResponse([]);
      if (path === "/api/action-items") return jsonResponse(decided ? [] : [judgement(proposal)]);
      if (path === "/api/action-items/action-1") return jsonResponse(judgementDetail(proposal));
      if (path === "/api/action-items/action-1/commands/approve" && init?.method === "POST") {
        decided = String(init?.body);
        return jsonResponse(judgement({ ...proposal, status: "resolved", allowed_commands: [] }));
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(navigation).getByRole("button", { name: "내 업무" }));

    // The proposal is one judgement among the rest, labelled by the server, not by the client.
    const panel = (await screen.findByText("판단이 필요한 업무")).closest(".decision-section") as HTMLElement;
    expect(await within(panel).findByText("AX가 만든 업무")).toBeTruthy();
    expect(within(panel).getByText("업무 생성")).toBeTruthy();

    fireEvent.click(within(panel).getByRole("button", { name: "판단하기" }));
    const drawer = await screen.findByRole("dialog", { name: "판단 상세" });
    expect(within(drawer).getByText("AX가 준비한 변경을 승인할지 결정하세요")).toBeTruthy();
    expect(within(drawer).getByText("담당")).toBeTruthy(); // the server's preview row
    fireEvent.click(within(drawer).getByRole("button", { name: "승인" }));

    await waitFor(() => expect(decided).not.toBeNull());
    expect(JSON.parse(String(decided))).toEqual({ expected_version: 4 });
    await waitFor(() => expect(within(panel).queryByText("AX가 만든 업무")).toBeNull());
  });

  it("shows an AX ActionItem without decision controls when action.decide is not granted", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/organization/members") {
        return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: "mina",
          display_name: "민아 (구성원)",
          organizations: [],
          capabilities: ["action.read"],
        });
      }
      if (path === "/api/my-work") return jsonResponse([]);
      if (path === "/api/conversations") {
        return jsonResponse([
          {
            conversation_id: "conversation-1",
            title: "권한 제한 대화",
            version: 2,
            messages: [],
            turns: [
              {
                turn_id: "turn-1",
                state: "completed",
                provider_run_ref: null,
                provider_session_ref: null,
                error: null,
              },
            ],
            context_references: [],
            tool_invocations: [],
            actions: [
              {
                action_id: "action-1",
                conversation_id: "conversation-1",
                turn_id: "turn-1",
                action_type: "task.create_self",
                title: "업무 만들기",
                state: "pending",
                commands: [],
                version: 1,
                payload_summary: "업무 만들기",
                result: null,
                audit_ref: null,
              },
            ],
          },
        ]);
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));

    expect((await screen.findAllByText("업무 만들기")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "승인" })).toBeNull();
    expect(screen.queryByRole("button", { name: "거절" })).toBeNull();
  });

  it("shows redacted Tool details and recorded latency in the AX timeline", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/organization/members") {
        return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: "mina",
          display_name: "민아 (구성원)",
          organizations: [],
          capabilities: ["task.read"],
        });
      }
      if (path === "/api/my-work") return jsonResponse([]);
      if (path === "/api/conversations") {
        return jsonResponse([
          {
            conversation_id: "conversation-1",
            title: "업무 확인",
            version: 1,
            messages: [],
            turns: [
              {
                turn_id: "turn-1",
                state: "completed",
                provider_run_ref: "provider-run-1",
                provider_session_ref: "provider-session-1",
                error: null,
              },
            ],
            context_references: [],
            tool_invocations: [
              {
                turn_id: "turn-1",
                sequence: 1,
                provider_call_id: "call-1",
                tool_name: "task_list",
                display_name: "내 업무 조회",
                input_summary: "현재 권한의 업무만 조회",
                state: "completed",
                result_summary: "업무 2건",
                error_summary: null,
                latency_ms: 321,
                target_resource_id: null,
                target_resource_version: null,
                audit_ref: null,
              },
            ],
            actions: [],
          },
        ]);
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));

    const receipt = await screen.findByRole("listitem", { name: "내 업무 조회 · 완료" });
    expect(receipt.closest(".ax-rail.terminal")).not.toBeNull();
    expect((receipt.closest("details") as HTMLDetailsElement).open).toBe(false);
    expect(within(receipt).getByText("321ms")).toBeTruthy();
    expect(screen.queryByText("요청 내용 확인 완료")).toBeNull();
    expect(receipt.querySelector("code")?.textContent).toBe("task_list");
  });

  it("restores an existing daily-report draft and submission history for the selected date", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/organization/members") {
        return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: "mina",
          display_name: "민아 (구성원)",
          organizations: [],
          capabilities: ["daily_report.generate"],
        });
      }
      if (path === "/api/my-work" || path === "/api/action-inbox" || path === "/api/actions") {
        return jsonResponse([]);
      }
      if (path.startsWith("/api/daily-reports/status")) {
        return jsonResponse({ report_date: "2026-09-03", status: "draft", report_id: "report-1" });
      }
      if (path === "/api/daily-reports/report-1/history") {
        return jsonResponse({
          report_id: "report-1",
          report_date: "2026-09-03",
          status: "draft",
          drafts: [
            {
              draft_id: "draft-1",
              version: 2,
              body: "다시 연 보고 초안",
              source_refs: [],
            },
          ],
          submissions: [
            {
              submission_id: "submission-1",
              version: 1,
              body: "이전 제출본",
              source_refs: [],
              reason: null,
              submitted_at: "2026-09-03T09:00:00+00:00",
            },
          ],
        });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(screen.getByRole("navigation", { name: "제품 탐색" })).getByRole("button", { name: "보고" }));

    expect((await screen.findByLabelText("일일보고 초안") as HTMLTextAreaElement).value).toBe(
      "다시 연 보고 초안",
    );
    expect(screen.getByText("제출 v1")).toBeTruthy();
    // Submitted at 09:00 UTC = 18:00 Seoul, rendered through the shared formatter, never a raw locale string.
    expect(screen.getByText("2026/09/03 18:00")).toBeTruthy();
    expect(screen.queryByText(/2026\. 9\. 3\.|오전|오후/)).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/daily-reports/report-1/history",
      expect.anything(),
    );
  });

  it("clears a stale report error as soon as the selected report date changes", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/organization/members") {
        return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: "mina",
          display_name: "민아 (구성원)",
          organizations: [],
          capabilities: ["daily_report.generate"],
        });
      }
      if (path === "/api/my-work" || path === "/api/action-inbox") return jsonResponse([]);
      if (path.startsWith(`/api/daily-reports/status?report_date=${seoulTodayForTest()}`)) {
        return new Response(JSON.stringify({ detail: "기존 날짜를 불러오지 못했습니다." }), { status: 500 });
      }
      if (path.startsWith(`/api/daily-reports/status?report_date=${otherReportDate()}`)) {
        return new Promise(() => {});
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(screen.getByRole("navigation", { name: "제품 탐색" })).getByRole("button", { name: "보고" }));
    expect((await screen.findByRole("alert")).textContent).toContain("기존 날짜를 불러오지 못했습니다.");

    // 보고일은 입력칸이 아니라 달력을 여는 트리거다 (DS-17) — 이 달 격자 안의 다른 날을 고른다
    fireEvent.click(screen.getByRole("button", { name: "보고일 달력 열기" }));
    const calendar = screen.getByRole("group", { name: "보고일" });
    fireEvent.click(calendar.querySelector(`[data-date="${otherReportDate()}"]`) as HTMLElement);
    await waitFor(() => {
      expect(screen.queryByRole("alert")).toBeNull();
    });
  });

  it("ignores a late same-id list snapshot after creating a Conversation", async () => {
    let resolveConversationList: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/organization/members") {
        return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: "mina",
          display_name: "민아 (구성원)",
          organizations: [],
          capabilities: ["action.read", "action.decide"],
        });
      }
      if (path === "/api/my-work" || path === "/api/actions") return jsonResponse([]);
      if (path === "/api/conversations" && init?.method === "POST") {
        return jsonResponse({
          conversation_id: "new-conversation",
          title: "새 대화",
          version: 2,
          messages: [
            {
              message_id: "message-1",
              turn_id: "turn-1",
              role: "user",
              body: "새 대화의 현재 발화",
              sequence: 1,
              state: "accepted",
            },
          ],
          turns: [
            {
              turn_id: "turn-1",
              state: "running",
              provider_run_ref: null,
              provider_session_ref: null,
              error: null,
            },
          ],
          context_references: [],
          tool_invocations: [],
          actions: [],
        });
      }
      if (path === "/api/conversations/new-conversation/messages" && init?.method === "POST") {
        return jsonResponse({ conversation_id: "new-conversation", message_id: "message-1", turn_id: "turn-1", queued: false, queue_size: 0 });
      }
      if (path === "/api/conversations/new-conversation") {
        return jsonResponse({
          conversation_id: "new-conversation",
          title: "새 대화",
          version: 2,
          messages: [{ message_id: "message-1", turn_id: "turn-1", role: "user", body: "새 대화의 현재 발화", sequence: 1, state: "accepted" }],
          turns: [{ turn_id: "turn-1", state: "running", provider_run_ref: null, provider_session_ref: null, error: null }],
          context_references: [],
          tool_invocations: [],
          actions: [],
        });
      }
      if (path === "/api/conversations") {
        return new Promise<Response>((resolve) => {
          resolveConversationList = resolve;
        });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([path, init]) => path === "/api/conversations" && !init?.method),
      ).toBe(true);
    });
    fireEvent.click(screen.getByRole("button", { name: "새 AX 대화" }));
    expect(fetchMock.mock.calls.some(([path, init]) => path === "/api/conversations" && init?.method === "POST")).toBe(false);
    fireEvent.change(screen.getByRole("textbox", { name: "AX 메시지" }), { target: { value: "새 대화의 현재 발화" } });
    fireEvent.click(screen.getByRole("button", { name: "보내기" }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/conversations",
        expect.objectContaining({ method: "POST" }),
      );
      expect(resolveConversationList).toBeTruthy();
    });

    resolveConversationList?.(
      jsonResponse([
        {
          conversation_id: "new-conversation",
          title: "새 대화",
          version: 1,
          messages: [],
          turns: [],
          context_references: [],
          tool_invocations: [],
        },
      ]),
    );

    expect(screen.getAllByText("새 대화의 현재 발화").length).toBeGreaterThanOrEqual(1);
    // 뒤늦게 온 목록이 진행 중인 회차를 지우지 않는다 — 상세가 도착하는 한 박자를 기다려 본다
    expect(await screen.findByText(/요청을 준비하는 중|대기열에서 기다리는 중/)).toBeTruthy();
  });

  it("applies only the latest overlapping Conversation list response", async () => {
    const listResolvers: Array<(response: Response) => void> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/organization/members") {
        return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: "mina",
          display_name: "민아 (구성원)",
          organizations: [],
          capabilities: [],
        });
      }
      if (path === "/api/my-work") return jsonResponse([]);
      if (path === "/api/conversations") {
        return new Promise<Response>((resolve) => listResolvers.push(resolve));
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    await waitFor(() => expect(listResolvers).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "닫기" }));
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    await waitFor(() => expect(listResolvers).toHaveLength(2));

    await act(async () => {
      listResolvers[1](jsonResponse([
        {
          conversation_id: "conversation-1",
          title: "최신 목록",
          version: 2,
          messages: [
            { message_id: "latest-user", turn_id: "latest-turn", role: "user", body: "최신 질문", sequence: 1, state: "accepted" },
            { message_id: "latest-answer", turn_id: "latest-turn", role: "assistant", body: "최신 답변", body_state: "final", sequence: 2, state: "accepted" },
          ],
          turns: [{ turn_id: "latest-turn", state: "completed", provider_run_ref: null, provider_session_ref: null, error: null }],
          context_references: [],
          tool_invocations: [],
          actions: [],
        },
      ]));
    });
    expect(await screen.findByText("최신 답변")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "대화 히스토리" }));
    expect(await screen.findByRole("button", { name: "최신 목록" })).toBeTruthy();

    await act(async () => {
      listResolvers[0](jsonResponse([
        {
          conversation_id: "conversation-1",
          title: "오래된 목록",
          version: 1,
          messages: [
            { message_id: "old-user", turn_id: "old-turn", role: "user", body: "오래된 질문", sequence: 1, state: "accepted" },
            { message_id: "old-answer", turn_id: "old-turn", role: "assistant", body: "오래된 답변", body_state: "final", sequence: 2, state: "accepted" },
          ],
          turns: [{ turn_id: "old-turn", state: "completed", provider_run_ref: null, provider_session_ref: null, error: null }],
          context_references: [],
          tool_invocations: [],
          actions: [],
        },
      ]));
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "최신 목록" })).toBeTruthy();
      expect(screen.queryByRole("button", { name: "오래된 목록" })).toBeNull();
    });
  });

  it("ignores an older overlapping active Conversation detail response", async () => {
    const detailResolvers: Array<(response: Response) => void> = [];
    const detail = (version: number, body: string, state: string) => ({
      conversation_id: "conversation-1",
      title: "상세 순서 확인",
      version,
      messages: [
        {
          message_id: `message-${version}`,
          turn_id: "turn-1",
          role: "user",
          body,
          sequence: 1,
          state: "accepted",
        },
      ],
      turns: [
        {
          turn_id: "turn-1",
          state,
          provider_run_ref: null,
          provider_session_ref: null,
          error: null,
        },
      ],
      context_references: [],
      tool_invocations: [],
      actions: [],
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/organization/members") {
        return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: "mina",
          display_name: "민아 (구성원)",
          organizations: [],
          capabilities: [],
        });
      }
      if (path === "/api/my-work") return jsonResponse([]);
      if (path === "/api/conversations") return jsonResponse([detail(1, "처음 상태", "running")]);
      if (path === "/api/conversations/conversation-1/messages") return jsonResponse({ queued: true });
      if (path === "/api/conversations/conversation-1") {
        return new Promise<Response>((resolve) => detailResolvers.push(resolve));
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    await waitFor(() => expect(detailResolvers).toHaveLength(1), { timeout: 2_500 });
    // A command refresh may overlap a poll; periodic reads themselves now wait for completion.
    fireEvent.change(screen.getByRole("textbox", { name: "AX 메시지" }), { target: { value: "추가 확인" } });
    fireEvent.click(screen.getByRole("button", { name: "대기열에 보내기" }));
    await waitFor(() => expect(detailResolvers).toHaveLength(2));

    detailResolvers[1](jsonResponse(detail(3, "최신 상세 상태", "completed")));
    expect(await screen.findByText("최신 상세 상태")).toBeTruthy();
    detailResolvers[0](jsonResponse(detail(2, "오래된 상세 상태", "running")));

    await waitFor(() => {
      expect(screen.getByText("최신 상세 상태")).toBeTruthy();
      expect(screen.queryByText("오래된 상세 상태")).toBeNull();
    });
  });

  it("keeps an equal-version active detail when a later list snapshot is older", async () => {
    const detailResolvers: Array<(response: Response) => void> = [];
    const listResolvers: Array<(response: Response) => void> = [];
    const conversation = (body: string, state: string, includeCompletedToolAndAction = false) => ({
      conversation_id: "conversation-1",
      title: "교차 요청 확인",
      version: 1,
      messages: [
        {
          message_id: `message-${body}`,
          turn_id: "turn-1",
          role: "user",
          body,
          sequence: 1,
          state: "accepted",
        },
        ...(includeCompletedToolAndAction ? [{
          message_id: "answer-1",
          turn_id: "turn-1",
          role: "assistant",
          body: "완료된 답변",
          body_state: "final",
          sequence: 2,
          state: "accepted",
        }] : []),
      ],
      turns: [
        {
          turn_id: "turn-1",
          state,
          provider_run_ref: null,
          provider_session_ref: null,
          error: null,
        },
      ],
      context_references: [],
      tool_invocations: includeCompletedToolAndAction
        ? [
            {
              turn_id: "turn-1",
              sequence: 1,
              provider_call_id: "call-1",
              tool_name: "task_list",
              display_name: "내 업무 조회",
              input_summary: "권한 범위 내 업무",
              state: "completed",
              result_summary: "업무 1건",
              error_summary: null,
              latency_ms: null,
              target_resource_id: null,
              target_resource_version: null,
              audit_ref: null,
            },
          ]
        : [],
      actions: includeCompletedToolAndAction
        ? [
            {
              action_id: "action-1",
              conversation_id: "conversation-1",
              turn_id: "turn-1",
              action_type: "task.create_self",
              title: "최신 판단 카드",
              state: "pending",
            commands: [{ id: "approve", label: "승인", tone: "primary" }, { id: "reject", label: "거절", tone: "neutral" }],
              version: 1,
              payload_summary: "최신 판단 카드",
              result: null,
              audit_ref: null,
            },
          ]
        : [],
    });
    let listRequests = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/organization/members") {
        return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: "mina",
          display_name: "민아 (구성원)",
          organizations: [],
          capabilities: [],
        });
      }
      if (path === "/api/my-work") return jsonResponse([]);
      if (path === "/api/conversations") {
        listRequests += 1;
        if (listRequests === 1) return jsonResponse([conversation("목록의 기존 상태", "running")]);
        return new Promise<Response>((resolve) => listResolvers.push(resolve));
      }
      if (path === "/api/conversations/conversation-1") {
        return new Promise<Response>((resolve) => detailResolvers.push(resolve));
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    await waitFor(() => expect(detailResolvers).toHaveLength(1), { timeout: 1_500 });
    fireEvent.click(screen.getByRole("button", { name: "닫기" }));
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    await waitFor(() => expect(listResolvers).toHaveLength(1));

    detailResolvers[0](jsonResponse(conversation("상세의 최신 상태", "completed", true)));
    expect(await screen.findByText("상세의 최신 상태")).toBeTruthy();
    listResolvers[0](jsonResponse([conversation("목록의 오래된 상태", "running")]));

    await new Promise((resolve) => window.setTimeout(resolve, 50));

    await waitFor(() => {
      expect(screen.getByText("상세의 최신 상태")).toBeTruthy();
      expect(screen.queryByText("목록의 오래된 상태")).toBeNull();
      expect(screen.getAllByText("최신 판단 카드").length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText("내 업무 조회")).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "대화 히스토리" }));
    fireEvent.click(screen.getByRole("button", { name: "교차 요청 확인" }));
    expect(screen.getByText("상세의 최신 상태")).toBeTruthy();
    expect(screen.queryByText("목록의 오래된 상태")).toBeNull();
    expect(screen.getAllByText("최신 판단 카드").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("내 업무 조회")).toBeTruthy();
  });

  it("keeps answered AX history while a new chat remains local until its first send", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/organization/members") {
        return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: "mina",
          display_name: "민아 (구성원)",
          organizations: [],
          capabilities: ["action.read"],
        });
      }
      if (path === "/api/my-work") return jsonResponse([]);
      if (path === "/api/conversations" && init?.method === "POST") {
        return jsonResponse({
          conversation_id: "new-conversation",
          title: "새 대화",
          version: 1,
          messages: [],
          turns: [],
          context_references: [],
          tool_invocations: [],
          actions: [],
        });
      }
      if (path === "/api/conversations/new-conversation/messages" && init?.method === "POST") {
        return jsonResponse({ conversation_id: "new-conversation", message_id: "new-message", turn_id: "new-turn", queued: false, queue_size: 0 });
      }
      if (path === "/api/conversations/new-conversation") {
        return jsonResponse({
          conversation_id: "new-conversation",
          title: "이번 주 내 업무를 정리해줘",
          version: 2,
          messages: [{ message_id: "new-message", turn_id: "new-turn", role: "user", body: "이번 주 내 업무를 정리해줘", sequence: 1, state: "accepted" }],
          turns: [{ turn_id: "new-turn", state: "running", provider_run_ref: null, provider_session_ref: null, error: null }],
          context_references: [],
          tool_invocations: [],
          actions: [],
        });
      }
      if (path === "/api/conversations") {
        return jsonResponse([
          {
            conversation_id: "existing-conversation",
            title: "기존 대화",
            version: 1,
            messages: [
              { message_id: "existing-user", turn_id: "existing-turn", role: "user", body: "기존 질문", sequence: 1, state: "accepted" },
              { message_id: "existing-answer", turn_id: "existing-turn", role: "assistant", body: "기존 답변", body_state: "final", sequence: 2, state: "accepted" },
            ],
            turns: [{ turn_id: "existing-turn", state: "completed", provider_run_ref: null, provider_session_ref: null, error: null }],
            context_references: [],
            tool_invocations: [],
            actions: [],
          },
        ]);
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    fireEvent.click(screen.getByRole("button", { name: "대화 히스토리" }));
    expect(await screen.findByRole("button", { name: "기존 대화" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "새 AX 대화" }));
    expect(await screen.findByRole("heading", { name: /무엇을 도와드릴까요/ })).toBeTruthy();
    expect(fetchMock.mock.calls.some(([path, init]) => path === "/api/conversations" && init?.method === "POST")).toBe(false);

    const starter = screen.getByRole("button", { name: "이번 주 내 업무를 정리해줘" });
    fireEvent.click(starter);
    fireEvent.click(starter);

    await waitFor(() => {
      expect(fetchMock.mock.calls.filter(([path, init]) => path === "/api/conversations" && init?.method === "POST")).toHaveLength(1);
      expect(fetchMock.mock.calls.filter(([path, init]) => path === "/api/conversations/new-conversation/messages" && init?.method === "POST")).toHaveLength(1);
    });
    const sendCall = fetchMock.mock.calls.find(([path, init]) => path === "/api/conversations/new-conversation/messages" && init?.method === "POST");
    expect(JSON.parse(String(sendCall?.[1]?.body))).toMatchObject({ body: "이번 주 내 업무를 정리해줘" });

    fireEvent.click(screen.getByRole("button", { name: "대화 히스토리" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "기존 대화" })).toBeTruthy();
      expect(screen.queryByRole("button", { name: "새 대화" })).toBeNull();
    });
  });

  it("does not let a late Conversation list replace an explicitly started new chat", async () => {
    let resolveConversationList: ((response: Response) => void) | undefined;
    const existing = {
      conversation_id: "existing-conversation",
      title: "기존 대화",
      version: 1,
      messages: [
        { message_id: "existing-user", turn_id: "existing-turn", role: "user", body: "기존 질문", sequence: 1, state: "accepted" },
        { message_id: "existing-answer", turn_id: "existing-turn", role: "assistant", body: "기존 답변", body_state: "final", sequence: 2, state: "accepted" },
      ],
      turns: [{ turn_id: "existing-turn", state: "completed", provider_run_ref: null, provider_session_ref: null, error: null }],
      context_references: [],
      tool_invocations: [],
      actions: [],
    };
    const created = {
      conversation_id: "new-conversation",
      title: "새 대화",
      version: 1,
      messages: [],
      turns: [],
      context_references: [],
      tool_invocations: [],
      actions: [],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/organization/members") return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      if (path === "/api/organization/me") {
        return jsonResponse({ member_id: "mina", display_name: "민아 (구성원)", organizations: [], capabilities: ["action.read"] });
      }
      if (path === "/api/my-work") return jsonResponse([]);
      if (path === "/api/conversations" && init?.method === "POST") return jsonResponse(created);
      if (path === "/api/conversations/new-conversation/messages" && init?.method === "POST") {
        return jsonResponse({ message_id: "new-message", turn_id: "new-turn", state: "queued" });
      }
      if (path === "/api/conversations/new-conversation") {
        return jsonResponse({
          ...created,
          version: 2,
          messages: [{ message_id: "new-message", turn_id: "new-turn", role: "user", body: "새 질문", sequence: 1, state: "accepted" }],
          turns: [{ turn_id: "new-turn", state: "running", provider_run_ref: null, provider_session_ref: null, error: null }],
        });
      }
      if (path === "/api/conversations") {
        return new Promise<Response>((resolve) => { resolveConversationList = resolve; });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    await waitFor(() => expect(resolveConversationList).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "새 AX 대화" }));

    resolveConversationList?.(jsonResponse([existing]));
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(screen.getByRole("heading", { name: /무엇을 도와드릴까요/ })).toBeTruthy();
    expect(screen.queryByText("기존 답변")).toBeNull();

    fireEvent.change(screen.getByRole("textbox", { name: "AX 메시지" }), { target: { value: "새 질문" } });
    fireEvent.click(screen.getByRole("button", { name: "보내기" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.filter(([path, init]) => path === "/api/conversations" && init?.method === "POST")).toHaveLength(1);
      expect(fetchMock.mock.calls.filter(([path, init]) => path === "/api/conversations/new-conversation/messages" && init?.method === "POST")).toHaveLength(1);
    });
  });

  it("keeps an in-flight canonical Conversation list when first-send session creation fails", async () => {
    let resolveConversationList: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/organization/members") {
        return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: "mina",
          display_name: "민아 (구성원)",
          organizations: [],
          capabilities: ["action.read"],
        });
      }
      if (path === "/api/my-work") return jsonResponse([]);
      if (path === "/api/conversations" && init?.method === "POST") {
        return new Response(JSON.stringify({ detail: "대화를 만들지 못했습니다." }), { status: 500 });
      }
      if (path === "/api/conversations") {
        return new Promise<Response>((resolve) => {
          resolveConversationList = resolve;
        });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    await waitFor(() => expect(resolveConversationList).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "새 AX 대화" }));
    fireEvent.change(screen.getByRole("textbox", { name: "AX 메시지" }), { target: { value: "새 질문" } });
    fireEvent.click(screen.getByRole("button", { name: "보내기" }));
    expect((await screen.findByRole("alert")).textContent).toContain("대화를 만들지 못했습니다.");

    await act(async () => {
      resolveConversationList?.(jsonResponse([
        {
          conversation_id: "existing-conversation",
          title: "기존 대화",
          version: 1,
          messages: [
            { message_id: "existing-user", turn_id: "existing-turn", role: "user", body: "기존 질문", sequence: 1, state: "accepted" },
            { message_id: "existing-answer", turn_id: "existing-turn", role: "assistant", body: "기존 답변", body_state: "final", sequence: 2, state: "accepted" },
          ],
          turns: [{ turn_id: "existing-turn", state: "completed", provider_run_ref: null, provider_session_ref: null, error: null }],
          context_references: [],
          tool_invocations: [],
          actions: [],
        },
      ]));
    });
    fireEvent.click(screen.getByRole("button", { name: "대화 히스토리" }));
    expect(await screen.findByRole("button", { name: "기존 대화" })).toBeTruthy();
  });

  it("invalidates an old persona's Conversation list and clears its local AX state on a persona switch", async () => {
    let resolveMinaList: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const personaId = new Headers(init?.headers).get("X-Demo-Persona");
      if (path === "/api/organization/members") {
        return jsonResponse([
          { id: "mina", display_name: "민아 (구성원)" },
          { id: "jiho", display_name: "지호 (팀장)" },
        ]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: personaId,
          display_name: personaId === "jiho" ? "지호 (팀장)" : "민아 (구성원)",
          organizations: [],
          capabilities: ["action.read"],
        });
      }
      if (path === "/api/my-work") return jsonResponse([]);
      if (path === "/api/conversations" && init?.method === "POST") {
        return jsonResponse({
          conversation_id: "mina-conversation",
          title: "민아의 임시 대화",
          version: 1,
          messages: [
            {
              message_id: "message-1",
              turn_id: null,
              role: "user",
              body: "민아의 현재 발화",
              sequence: 1,
              state: "queued",
            },
          ],
          turns: [],
          context_references: [],
          tool_invocations: [],
          actions: [],
        });
      }
      if (path === "/api/conversations" && personaId === "mina") {
        return new Promise<Response>((resolve) => {
          resolveMinaList = resolve;
        });
      }
      if (path === "/api/conversations" && personaId === "jiho") return jsonResponse([]);
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    await waitFor(() => expect(resolveMinaList).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "새 AX 대화" }));
    fireEvent.change(screen.getByRole("textbox", { name: "AX 메시지" }), { target: { value: "민아의 현재 발화" } });
    fireEvent.click(screen.getByRole("button", { name: "보내기" }));
    expect(await screen.findByText("민아의 현재 발화")).toBeTruthy();

    await switchAccount("jiho");
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    await waitFor(() => {
      expect(screen.queryByText("민아의 현재 발화")).toBeNull();
    });
    resolveMinaList?.(
      jsonResponse([
        {
          conversation_id: "mina-conversation",
          title: "민아의 임시 대화",
          version: 1,
          messages: [],
          turns: [],
          context_references: [],
          tool_invocations: [],
          actions: [],
        },
      ]),
    );

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "민아의 임시 대화" })).toBeNull();
      expect(screen.queryByText("민아의 현재 발화")).toBeNull();
    });
  });

  it("does not restore a stale persona's delayed Conversation detail, Action, or Tool after switching", async () => {
    let resolveMinaDetail: ((response: Response) => void) | undefined;
    let resolveJihoList: ((response: Response) => void) | undefined;
    const minaConversation = {
      conversation_id: "mina-conversation",
      title: "민아의 비공개 대화",
      version: 1,
      messages: [],
      turns: [{ turn_id: "turn-1", state: "running", provider_run_ref: null, provider_session_ref: null, error: null }],
      context_references: [],
      tool_invocations: [],
      actions: [],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const personaId = new Headers(init?.headers).get("X-Demo-Persona");
      if (path === "/api/organization/members") return jsonResponse([{ id: "mina", display_name: "민아" }, { id: "jiho", display_name: "지호" }]);
      if (path === "/api/organization/me") return jsonResponse({ member_id: personaId, display_name: personaId, organizations: [], capabilities: ["action.read"] });
      if (path === "/api/my-work" || path === "/api/actions") return jsonResponse([]);
      if (path === "/api/conversations" && personaId === "mina") return jsonResponse([minaConversation]);
      if (path === "/api/conversations" && personaId === "jiho") {
        return new Promise<Response>((resolve) => { resolveJihoList = resolve; });
      }
      if (path === "/api/conversations/mina-conversation") return new Promise<Response>((resolve) => { resolveMinaDetail = resolve; });
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    await waitFor(() => expect(resolveMinaDetail).toBeTruthy(), { timeout: 2_500 });
    await switchAccount("jiho");
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    await waitFor(() => expect(resolveJihoList).toBeTruthy());
    await act(async () => {
      resolveJihoList?.(jsonResponse([{
        ...minaConversation,
        conversation_id: "jiho-conversation",
        title: "지호의 대화",
        messages: [
          { message_id: "jiho-user", turn_id: "jiho-turn", role: "user", body: "지호 질문", sequence: 1, state: "accepted" },
          { message_id: "jiho-answer", turn_id: "jiho-turn", role: "assistant", body: "지호 답변", body_state: "final", sequence: 2, state: "accepted" },
        ],
        turns: [{ turn_id: "jiho-turn", state: "completed", provider_run_ref: null, provider_session_ref: null, error: null }],
      }]));
    });
    fireEvent.click(screen.getByRole("button", { name: "대화 히스토리" }));
    await screen.findByRole("button", { name: "지호의 대화" });
    await act(async () => {
      resolveMinaDetail?.(jsonResponse({
        ...minaConversation,
        messages: [{ message_id: "secret-message", turn_id: "turn-1", role: "user", body: "민아의 비공개 본문", sequence: 1, state: "accepted" }],
        turns: [{ turn_id: "turn-1", state: "completed", provider_run_ref: null, provider_session_ref: null, error: null }],
        tool_invocations: [{ turn_id: "turn-1", sequence: 1, provider_call_id: null, tool_name: "task_list", display_name: "민아 도구", input_summary: "비공개", state: "completed", result_summary: "비공개 결과", error_summary: null, latency_ms: null, target_resource_id: null, target_resource_version: null, audit_ref: null }],
        actions: [{ action_id: "secret-action", conversation_id: "mina-conversation", turn_id: "turn-1", action_type: "task.create_self", title: "민아 판단", state: "pending", version: 1, payload_summary: "비공개 변경", result: null, audit_ref: null }],
      }));
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "민아의 비공개 대화" })).toBeNull();
      expect(screen.getByRole("button", { name: "지호의 대화" })).toBeTruthy();
      expect(screen.queryByText("민아의 비공개 본문")).toBeNull();
      expect(screen.queryByText("민아 도구 · 완료")).toBeNull();
      expect(screen.queryByText("민아 판단")).toBeNull();
    });
  });

  it("polls an open AX drawer until a completed turn projects its pending ActionItem", async () => {
    let conversationReads = 0;
    const runningConversation = {
      conversation_id: "conversation-1",
      title: "새 대화",
      version: 2,
      messages: [
        {
          message_id: "message-1",
          turn_id: "turn-1",
          role: "user",
          body: "업무 요청을 만들어줘",
          sequence: 1,
          state: "accepted",
        },
      ],
      turns: [
        {
          turn_id: "turn-1",
          state: "running",
          provider_run_ref: null,
          provider_session_ref: null,
          error: null,
        },
      ],
      context_references: [],
      tool_invocations: [],
      actions: [],
    };
    const completedConversation = {
      ...runningConversation,
      version: 3,
      turns: [{ ...runningConversation.turns[0], state: "completed" }],
      actions: [
        {
          action_id: "action-1",
          conversation_id: "conversation-1",
          turn_id: "turn-1",
          action_type: "work_request.create",
          title: "업무 요청 생성 확인",
          state: "pending",
          version: 1,
          payload_summary: "업무 요청: 고객 요청 확인",
          result: null,
          audit_ref: null,
        },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/organization/members") {
        return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: "mina",
          display_name: "민아 (구성원)",
          organizations: [],
          capabilities: ["action.read", "action.decide", "work_request.create"],
        });
      }
      if (path === "/api/my-work" || path === "/api/actions") return jsonResponse([]);
      if (path === "/api/conversations" && init?.method === "POST") {
        return jsonResponse(runningConversation);
      }
      if (path === "/api/conversations/conversation-1/messages" && init?.method === "POST") {
        return jsonResponse({ conversation_id: "conversation-1", message_id: "message-1", turn_id: "turn-1", queued: false, queue_size: 0 });
      }
      if (path === "/api/conversations") return jsonResponse([]);
      if (path === "/api/conversations/conversation-1") {
        conversationReads += 1;
        return jsonResponse(conversationReads === 1 ? runningConversation : completedConversation);
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([path, init]) => path === "/api/conversations" && !init?.method),
      ).toBe(true);
    });
    fireEvent.click(screen.getByRole("button", { name: "새 AX 대화" }));
    fireEvent.change(screen.getByRole("textbox", { name: "AX 메시지" }), { target: { value: "업무 요청을 만들어줘" } });
    fireEvent.click(screen.getByRole("button", { name: "보내기" }));
    expect(await screen.findByText(/요청을 준비하는 중|대기열에서 기다리는 중/)).toBeTruthy();
    expect(await screen.findByText("업무 요청 생성 확인", {}, { timeout: 2_000 })).toBeTruthy();
    expect(conversationReads).toBeGreaterThanOrEqual(1);
  });

  it("keeps the AX composer enabled, sends an idempotent queued fragment, and attaches typed current-screen context", async () => {
    const conversation = {
      conversation_id: "conversation-1",
      title: "업무 확인",
      version: 1,
      messages: [
        {
          message_id: "message-1",
          turn_id: "turn-1",
          role: "user",
          body: "현재 업무를 확인해줘",
          sequence: 1,
          state: "accepted",
        },
      ],
      turns: [
        {
          turn_id: "turn-1",
          state: "running",
          provider_run_ref: null,
          provider_session_ref: null,
          error: null,
        },
      ],
      context_references: [],
      tool_invocations: [],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/organization/members") {
        return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: "mina",
          display_name: "민아 (구성원)",
          organizations: [],
          capabilities: ["daily_report.generate"],
        });
      }
      if (path === "/api/my-work") {
        return jsonResponse([
          {
            task_id: "task-1",
            title: "첨부할 현재 업무",
            state: "in_progress",
            version: 3,
            block_reason: null,
          },
        ]);
      }
      if (path === "/api/work-request-assignee-candidates") return jsonResponse([]);
      if (path === "/api/conversations") return jsonResponse([conversation]);
      if (path === "/api/conversations/conversation-1" && init?.method === "POST") {
        return jsonResponse({
          conversation_id: "conversation-1",
          message_id: "message-2",
          turn_id: null,
          queued: true,
          queue_size: 1,
        });
      }
      if (path === "/api/conversations/conversation-1") return jsonResponse(conversation);
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(screen.getByRole("navigation", { name: "제품 탐색" })).getByRole("button", { name: "내 업무" }));
    fireEvent.click(await screen.findByText("첨부할 현재 업무"));
    fireEvent.click(await screen.findByRole("button", { name: /AX에게 이 업무 묻기/ }));

    await screen.findByText("업무 확인");
    expect(screen.getByText(/업무 · 첨부할 현재 업무/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("AX 메시지"), { target: { value: "이 업무를 이어서 진행할게" } });

    const send = screen.getByRole("button", { name: "대기열에 보내기" });
    expect(send.hasAttribute("disabled")).toBe(false);
    fireEvent.click(send);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/conversations/conversation-1/messages",
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({ "Idempotency-Key": expect.any(String) }),
        }),
      );
    });
    const request = fetchMock.mock.calls.find(([path, init]) =>
      path === "/api/conversations/conversation-1/messages" && init?.method === "POST",
    );
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({
      body: "이 업무를 이어서 진행할게",
      context: [
        {
          resource_type: "task",
          resource_id: "task-1",
          resource_version: 3,
          included: true,
        },
      ],
    });
  });
  it("re-reads the current My Work projection after approving an AX action from the chat without losing the view filter", async () => {
    let approved = false;
    const myWorkReads: number[] = [];
    const conversation = {
      conversation_id: "conversation-1",
      title: "새 대화",
      version: 3,
      messages: [{ message_id: "m1", turn_id: "turn-1", role: "user", body: "업무 하나 만들어줘", sequence: 1, state: "accepted" }],
      turns: [{ turn_id: "turn-1", state: "completed", progress_state: "completed", provider_run_ref: null, provider_session_ref: null, error: null }],
      context_references: [],
      tool_invocations: [],
      actions: [] as unknown[],
    };
    const action = () => ({
      action_id: "action-9",
      conversation_id: "conversation-1",
      turn_id: "turn-1",
      action_type: "task.create_self",
      title: "업무 생성 확인",
      subject: "AX가 만든 업무",
      operation_label: "업무 생성",
      preview: [{ id: "assignee", label: "담당", value: "민아 (구성원)", kind: "person" }],
      state: approved ? "approved" : "pending",
      version: approved ? 2 : 1,
      payload_summary: "업무 생성 확인",
      result: approved ? { task_id: "task-9" } : null,
      audit_ref: approved ? "action-9" : null,
      commands: approved ? [] : [{ id: "approve", label: "승인", tone: "primary" }, { id: "reject", label: "거절", tone: "neutral" }],
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/organization/members") return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      if (path === "/api/organization/me") {
        return jsonResponse({ member_id: "mina", display_name: "민아 (구성원)", organizations: [], capabilities: ["task.read", "task.self_manage", "action.read", "action.decide"] });
      }
      if (path === "/api/my-work") {
        myWorkReads.push(Date.now());
        return jsonResponse(approved ? [{ task_id: "task-9", title: "AX가 만든 업무", state: "open", version: 1, block_reason: null }] : []);
      }
      if (path === "/api/actions") return jsonResponse([action()]);
      if (path === "/api/action-items") {
        return jsonResponse(approved ? [] : [judgement({ action_item_id: "action-9", kind: "ax.task.create_self", subject: "AX가 만든 업무", operation_label: "업무 생성", resource: { type: "action", id: "action-9" } })]);
      }
      if (path === "/api/conversations") return jsonResponse([{ ...conversation, actions: [action()] }]);
      if (path === "/api/conversations/conversation-1") return jsonResponse({ ...conversation, version: approved ? 4 : 3, actions: [action()] });
      if (path === "/api/actions/action-9/decide" && init?.method === "POST") {
        approved = true;
        return jsonResponse(action());
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(navigation).getByRole("button", { name: "내 업무" }));
    // 상태 필터는 v2 05 의 툴바 칩 + 팝오버다 (select 아님)
    fireEvent.click(await screen.findByRole("button", { name: "진행 중·시작 전·막힘" }));
    fireEvent.click(screen.getByRole("radio", { name: "전체 상태" }));
    expect(screen.getByRole("button", { name: "전체 상태" })).toBeTruthy();
    const readsBeforeApproval = myWorkReads.length;

    fireEvent.click(screen.getByRole("button", { name: "AX" }));
    const card = (await screen.findByText("AX가 만든 업무", { selector: ".ax-action-card b" })).closest(".ax-action-card") as HTMLElement;
    expect(within(card).getByText("담당")).toBeTruthy(); // server preview row, not inferred from action_type
    // The same judgement is also in the unified decision ledger, labelled by the server.
    const panel = document.querySelector(".decision-section") as HTMLElement;
    expect(within(panel).getByText("AX가 만든 업무")).toBeTruthy();
    expect(within(panel).getByText("업무 생성")).toBeTruthy();
    fireEvent.click(within(card).getByRole("button", { name: "승인" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/actions/action-9/decide", expect.objectContaining({ method: "POST" }));
    });
    // The current surface re-reads its projection and shows the created task by its real title...
    await waitFor(() => expect(myWorkReads.length).toBeGreaterThan(readsBeforeApproval));
    await waitFor(() => {
      const onWorkSurface = screen.getAllByText("AX가 만든 업무").filter((node) => !node.closest(".ax-action-card") && !node.closest(".decision-panel, .decision-section"));
      expect(onWorkSurface.length).toBeGreaterThan(0);
    });
    // ...without a remount: the filter the user chose is still selected.
    expect(screen.getByRole("button", { name: "전체 상태" })).toBeTruthy();
    expect(screen.queryByText("판단은 저장되었지만 화면을 갱신하지 못했습니다.")).toBeNull();
  });
  it("names the real work subject in the approval notice, not the generic operation title", async () => {
    let approved = false;
    const action = () => ({
      action_id: "action-7",
      conversation_id: "conversation-1",
      turn_id: "turn-1",
      action_type: "task.create_self",
      title: "업무 생성 확인",
      subject: "분기 리포트 정리",
      operation_label: "업무 생성",
      preview: [{ id: "assignee", label: "담당", value: "민아 (구성원)", kind: "person" }],
      state: approved ? "approved" : "pending",
      version: approved ? 2 : 1,
      payload_summary: "업무 생성 확인",
      result: null,
      audit_ref: null,
      commands: approved ? [] : [{ id: "approve", label: "승인", tone: "primary" }],
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/organization/members") return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      if (path === "/api/organization/me") {
        return jsonResponse({ member_id: "mina", display_name: "민아 (구성원)", organizations: [], capabilities: ["task.read", "task.self_manage", "action.read", "action.decide"] });
      }
      if (path === "/api/my-work") return jsonResponse([]);
      if (path === "/api/tasks?include_closed=true") return jsonResponse([]);
      if (path === "/api/work-requests") return jsonResponse([]);
      if (path === "/api/actions") return jsonResponse([action()]);
      if (path === "/api/action-items/action-7") {
        return jsonResponse(judgementDetail({ action_item_id: "action-7", kind: "ax.task.create_self", subject: "분기 리포트 정리", operation_label: "업무 생성", resource: { type: "action", id: "action-7" }, allowed_commands: [{ id: "approve", label: "승인", tone: "primary", requires_reason: false }] }));
      }
      if (path === "/api/action-items/action-7/commands/approve" && init?.method === "POST") {
        approved = true;
        return jsonResponse(judgement({ action_item_id: "action-7", status: "resolved", allowed_commands: [] }));
      }
      if (path === "/api/action-items") {
        return jsonResponse(approved ? [] : [judgement({ action_item_id: "action-7", kind: "ax.task.create_self", subject: "분기 리포트 정리", operation_label: "업무 생성", resource: { type: "action", id: "action-7" } })]);
      }
      if (path === "/api/action-inbox") return jsonResponse([]);
      if (path === "/api/actions/action-7/decide" && init?.method === "POST") {
        approved = true;
        return jsonResponse(action());
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(navigation).getByRole("button", { name: "내 업무" }));
    const card = (await screen.findByText("분기 리포트 정리")).closest(".task-card") as HTMLElement;
    expect(card.querySelector(".task-card-kicker")?.textContent).toBe("업무 생성");
    fireEvent.click(within(card).getByRole("button", { name: "판단하기" }));
    const drawer = await screen.findByRole("dialog", { name: "판단 상세" });
    fireEvent.click(await within(drawer).findByRole("button", { name: "승인" }));

    // The notice names what was actually judged, matching the card the approver just read.
    expect(await screen.findByText("'분기 리포트 정리' 판단을 반영했습니다.")).toBeTruthy();
    expect(screen.queryByText(/'업무 생성 확인'/)).toBeNull();
  });
  it("waits for every affected projection to settle before reporting an approval, and keeps the surface state", async () => {
    let approved = false;
    let releaseMyWork: (() => void) | null = null;
    let holdMyWork = true;
    const order: string[] = [];
    const action = () => ({
      action_id: "action-8",
      conversation_id: "conversation-1",
      turn_id: "turn-1",
      action_type: "task.create_self",
      title: "업무 생성 확인",
      subject: "정산 자료 정리",
      operation_label: "업무 생성",
      preview: [{ id: "assignee", label: "담당", value: "민아 (구성원)", kind: "person" }],
      state: approved ? "approved" : "pending",
      version: approved ? 2 : 1,
      payload_summary: "업무 생성 확인",
      result: null,
      audit_ref: null,
      commands: approved ? [] : [{ id: "approve", label: "승인", tone: "primary" }],
    });
    const conversation = () => ({
      conversation_id: "conversation-1",
      title: "새 대화",
      version: approved ? 4 : 3,
      messages: [{ message_id: "m1", turn_id: "turn-1", role: "user", body: "업무 만들어줘", sequence: 1, state: "accepted" }],
      turns: [{ turn_id: "turn-1", state: "completed", progress_state: "completed", provider_run_ref: null, provider_session_ref: null, error: null }],
      context_references: [],
      tool_invocations: [],
      actions: [action()],
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/organization/members") return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      if (path === "/api/organization/me") {
        return jsonResponse({ member_id: "mina", display_name: "민아 (구성원)", organizations: [], capabilities: ["task.read", "task.self_manage", "action.read", "action.decide"] });
      }
      if (path === "/api/my-work") {
        if (approved && holdMyWork) {
          holdMyWork = false;
          // Hold the first post-approval surface read open so the assertion can prove the toast waits for it.
          await new Promise<void>((resolve) => {
            releaseMyWork = () => {
              order.push("my-work settled");
              resolve();
            };
          });
        }
        return jsonResponse(approved ? [{ task_id: "task-8", title: "정산 자료 정리", state: "open", version: 1, block_reason: null }] : []);
      }
      if (path === "/api/tasks?include_closed=true") return jsonResponse([]);
      if (path === "/api/work-requests") return jsonResponse([]);
      if (path === "/api/action-inbox") return jsonResponse([]);
      if (path === "/api/actions") return jsonResponse([action()]);
      if (path === "/api/conversations") return jsonResponse([conversation()]);
      if (path === "/api/conversations/conversation-1") return jsonResponse(conversation());
      if (path === "/api/actions/action-8/decide" && init?.method === "POST") {
        approved = true;
        order.push("decided");
        return jsonResponse(action());
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(navigation).getByRole("button", { name: "내 업무" }));
    fireEvent.click(await screen.findByRole("button", { name: "진행 중·시작 전·막힘" }));
    fireEvent.click(screen.getByRole("radio", { name: "전체 상태" }));

    fireEvent.click(screen.getByRole("button", { name: "AX" }));
    const card = (await screen.findByText("정산 자료 정리", { selector: ".ax-action-card b" })).closest(".ax-action-card") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "승인" }));

    await waitFor(() => expect(order).toContain("decided"));
    // The decision is persisted, but the surface read is still in flight: no success message yet.
    await waitFor(() => expect(releaseMyWork).not.toBeNull());
    expect(screen.queryByText("제안을 승인해 반영했습니다.")).toBeNull();

    releaseMyWork!();
    expect(await screen.findByText("제안을 승인해 반영했습니다.")).toBeTruthy();
    expect(order).toEqual(["decided", "my-work settled"]);
    // Settled in place: the created task is visible and the chosen filter survived.
    await waitFor(() => {
      const onSurface = screen.getAllByText("정산 자료 정리").filter((node) => !node.closest(".ax-action-card") && !node.closest(".decision-panel, .decision-section"));
      expect(onSurface.length).toBeGreaterThan(0);
    });
    expect(screen.getByRole("button", { name: "전체 상태" })).toBeTruthy();
    expect(screen.queryByText("판단은 저장되었지만 화면을 갱신하지 못했습니다.")).toBeNull();
  });

  it("keeps a failed projection refresh distinct from a failed approval and offers a retry", async () => {
    let approved = false;
    let surfaceReadFails = true;
    const action = () => ({
      action_id: "action-6",
      conversation_id: "conversation-1",
      turn_id: "turn-1",
      action_type: "task.create_self",
      title: "업무 생성 확인",
      subject: "월말 정산",
      operation_label: "업무 생성",
      preview: [{ id: "assignee", label: "담당", value: "민아 (구성원)", kind: "person" }],
      state: approved ? "approved" : "pending",
      version: approved ? 2 : 1,
      payload_summary: "업무 생성 확인",
      result: null,
      audit_ref: null,
      commands: approved ? [] : [{ id: "approve", label: "승인", tone: "primary" }],
    });
    const conversation = () => ({
      conversation_id: "conversation-1",
      title: "새 대화",
      version: 3,
      messages: [{ message_id: "m1", turn_id: "turn-1", role: "user", body: "업무 만들어줘", sequence: 1, state: "accepted" }],
      turns: [{ turn_id: "turn-1", state: "completed", progress_state: "completed", provider_run_ref: null, provider_session_ref: null, error: null }],
      context_references: [],
      tool_invocations: [],
      actions: [action()],
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/organization/members") return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      if (path === "/api/organization/me") {
        return jsonResponse({ member_id: "mina", display_name: "민아 (구성원)", organizations: [], capabilities: ["task.read", "task.self_manage", "action.read", "action.decide"] });
      }
      if (path === "/api/my-work") {
        if (approved && surfaceReadFails) return new Response("upstream unavailable", { status: 503 });
        return jsonResponse(approved ? [{ task_id: "task-6", title: "월말 정산", state: "open", version: 1, block_reason: null }] : []);
      }
      if (path === "/api/tasks?include_closed=true") return jsonResponse([]);
      if (path === "/api/work-requests") return jsonResponse([]);
      if (path === "/api/action-inbox") return jsonResponse([]);
      if (path === "/api/actions") return jsonResponse([action()]);
      if (path === "/api/conversations") return jsonResponse([conversation()]);
      if (path === "/api/conversations/conversation-1") return jsonResponse(conversation());
      if (path === "/api/actions/action-6/decide" && init?.method === "POST") {
        approved = true;
        return jsonResponse(action());
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(navigation).getByRole("button", { name: "내 업무" }));
    await screen.findByRole("button", { name: "진행 중·시작 전·막힘" });
    fireEvent.click(screen.getByRole("button", { name: "AX" }));
    const card = (await screen.findByText("월말 정산", { selector: ".ax-action-card b" })).closest(".ax-action-card") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "승인" }));

    // The approval succeeded: it is reported as such, and the refresh failure is a separate, retryable banner.
    expect(await screen.findByText("판단은 저장되었지만 화면을 갱신하지 못했습니다.")).toBeTruthy();
    // Persisted-decision wording only: the task may not be visible, so nothing may claim it was reflected on screen.
    expect(await screen.findByText("제안을 승인했습니다.")).toBeTruthy();
    expect(screen.queryByText("제안을 승인해 반영했습니다.")).toBeNull();
    expect(screen.queryByText("AX 확인 항목을 처리하지 못했습니다.")).toBeNull();
    expect(fetchMock.mock.calls.filter(([path]) => String(path) === "/api/actions/action-6/decide")).toHaveLength(1);

    // Retrying re-reads the projections without deciding again.
    surfaceReadFails = false;
    fireEvent.click(screen.getByRole("button", { name: "다시 불러오기" }));
    await waitFor(() => expect(screen.queryByText("판단은 저장되었지만 화면을 갱신하지 못했습니다.")).toBeNull());
    expect(fetchMock.mock.calls.filter(([path]) => String(path) === "/api/actions/action-6/decide")).toHaveLength(1);
    await waitFor(() => {
      const onSurface = screen.getAllByText("월말 정산").filter((node) => !node.closest(".ax-action-card") && !node.closest(".decision-panel, .decision-section"));
      expect(onSurface.length).toBeGreaterThan(0);
    });
  });

  it("runs the server-authored assignment cancellation command and reports cancellation", async () => {
    let cancelled = false;
    const action = () => ({
      action_id: "action-cancel",
      conversation_id: "conversation-cancel",
      turn_id: "turn-cancel",
      action_type: "task.assign",
      title: "업무 요청 확인",
      subject: "상대 확인 전 요청",
      operation_label: "업무 요청",
      preview: [
        { id: "assignee", label: "담당자", value: "민아 (구성원)", kind: "person" },
        { id: "requester", label: "요청자", value: "지호 (팀장)", kind: "person" },
      ],
      state: "approved",
      version: 2,
      payload_summary: "업무 요청 확인",
      result: { assignment_id: "assignment-1", status: cancelled ? "cancelled" : "pending", task: { task_id: "task-1" } },
      audit_ref: "action-cancel",
      commands: cancelled ? [] : [{ id: "cancel_assignment", label: "취소", tone: "danger" }],
      edit_contract: {
        editor: "task",
        base_submission_version: 1,
        values: { title: "상대 확인 전 요청", assignee_id: "mina" },
        fields: [],
        warnings: [],
      },
      material_drafts: [],
    });
    const conversation = () => ({
      conversation_id: "conversation-cancel",
      title: "업무 요청",
      version: cancelled ? 4 : 3,
      messages: [{ message_id: "m1", turn_id: "turn-cancel", role: "user", body: "민아에게 요청해줘", sequence: 1, state: "accepted" }],
      turns: [{ turn_id: "turn-cancel", state: "completed", progress_state: "completed", provider_run_ref: null, provider_session_ref: null, error: null }],
      context_references: [],
      tool_invocations: [],
      actions: [action()],
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/organization/me") {
        return jsonResponse({ member_id: "jiho", display_name: "지호 (팀장)", organizations: [], capabilities: ["task.read", "task.assign", "action.read", "action.decide"] });
      }
      if (path === "/api/organization/members") return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      if (path === "/api/conversations") return jsonResponse([conversation()]);
      if (path === "/api/conversations/conversation-cancel") return jsonResponse(conversation());
      if (path === "/api/actions") return jsonResponse([action()]);
      if (path === "/api/action-items") return jsonResponse([]);
      if (path === "/api/tasks?include_closed=true" || path === "/api/work-requests" || path === "/api/meetings") return jsonResponse([]);
      if (path === "/api/action-items/action-cancel/commands/cancel_assignment" && init?.method === "POST") {
        cancelled = true;
        return jsonResponse({});
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock, "jiho"));

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    const card = (await screen.findByText("상대 확인 전 요청", { selector: ".action-task-summary b" })).closest(".action-task-card") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "취소" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/action-items/action-cancel/commands/cancel_assignment",
      expect.objectContaining({ method: "POST" }),
    ));
    expect(await screen.findByText("업무 요청을 취소했습니다.", { selector: ".toast" })).toBeTruthy();
    await waitFor(() => expect(within(card).queryByRole("button", { name: "취소" })).toBeNull());
  });
});
