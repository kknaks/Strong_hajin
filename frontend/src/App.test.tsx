import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

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
    if (path === "/api/auth/providers") {
      const personas = await ((await fetchMock("/api/developer/personas", withPersona(init))) as Response).json();
      return jsonResponse({ developer: true, oidc: false, accounts: personas });
    }
    if (path === "/api/auth/login") {
      current = (JSON.parse(String(init?.body)) as { account: string }).account;
      return (await fetchMock("/api/organization/me", withPersona({ ...init, method: "GET", body: undefined }))) as Response;
    }
    if (path === "/api/auth/logout") {
      current = null;
      return new Response(null, { status: 204 });
    }
    if (path === "/api/auth/me") {
      if (!current) return new Response(JSON.stringify({ detail: "로그인이 필요합니다." }), { status: 401 });
      return (await fetchMock("/api/organization/me", withPersona(init))) as Response;
    }
    return (await fetchMock(path, withPersona(init))) as Response;
  };
}

async function switchAccount(personaId: string, displayName: string) {
  fireEvent.click(screen.getByRole("button", { name: "로그아웃" }));
  fireEvent.click(await screen.findByRole("radio", { name: (name) => name.startsWith(displayName) }));
  fireEvent.click(screen.getByRole("button", { name: "로그인" }));
  await screen.findByRole("navigation", { name: "제품 탐색" });
}

describe("product surfaces", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows an authorized direct task on Today and advances it from My Work", async () => {
    let taskState = "open";
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);

      if (path === "/api/developer/personas") {
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
          { assignment_id: "assignment-1", title: "수락된 배정", state: "active" },
        ]);
      }

      if (path === "/api/work-request-assignee-candidates") {
        return jsonResponse([{ id: "jiho", display_name: "지호 (팀장)" }]);
      }
      if (path === "/api/actions") return jsonResponse([]);
      if (path === "/api/action-inbox") {
        return jsonResponse([
          {
            request_id: "request-1",
            title: "오늘 확인할 업무 요청",
            state: "pending",
            version: 1,
            task_id: null,
            assignment_state: null,
            conditions: null,
          },
        ]);
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
      if (path === "/api/developer/personas") {
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
      if (path === "/api/developer/personas") {
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

    await switchAccount("jiho", "지호 (팀장)");
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

      if (path === "/api/developer/personas") {
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
      if (path === "/api/action-inbox") {
        return jsonResponse(personaId === "jiho" && request?.state === "pending" ? [request] : []);
      }
      if (path === "/api/actions") return jsonResponse([]);
      if (path === "/api/work-requests/request-1/accept" && init?.method === "POST" && request) {
        request = { ...request, state: "accepted", version: 2, task_id: "task-1", assignment_state: "active" };
        return jsonResponse(request);
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(screen.getByRole("navigation", { name: "제품 탐색" })).getByRole("button", { name: "내 업무" }));
    fireEvent.click(await screen.findByRole("button", { name: "새 업무 추가" }));
    fireEvent.click(screen.getByRole("tab", { name: "요청" }));
    await screen.findByLabelText("담당 후보");
    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "UI로 만든 업무 요청" } });
    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/work-requests",
        expect.objectContaining({ method: "POST" }),
      );
    });

    await switchAccount("jiho", "지호 (팀장)");
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
    fireEvent.click(within(screen.getByRole("navigation", { name: "제품 탐색" })).getByRole("button", { name: "판단" }));
    expect(await screen.findByText("UI로 만든 업무 요청")).toBeTruthy();
    expect(screen.getByRole("button", { name: "수락" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "수락" }));

    fireEvent.click(within(screen.getByRole("navigation", { name: "제품 탐색" })).getByRole("button", { name: "내 업무" }));
    expect(await screen.findByText("UI로 만든 업무 요청")).toBeTruthy();
  });

  it("shows a pending AX action in the decision surface and decides it with its version", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/developer/personas") {
        return jsonResponse([{ id: "mina", display_name: "민아 (구성원)" }]);
      }
      if (path === "/api/organization/me") {
        return jsonResponse({
          member_id: "mina",
          display_name: "민아 (구성원)",
          organizations: [],
          capabilities: ["action.read", "action.decide", "daily_report.generate"],
        });
      }
      if (path === "/api/my-work") return jsonResponse([]);
      if (path === "/api/work-request-assignee-candidates") return jsonResponse([]);
      if (path === "/api/action-inbox") return jsonResponse([]);
      if (path === "/api/actions") {
        return jsonResponse([
          {
            action_id: "action-1",
            conversation_id: "conversation-1",
            turn_id: "turn-1",
            action_type: "task.create_self",
            title: "업무 만들기",
            state: "pending",
            version: 4,
            payload_summary: "업무 만들기",
            result: null,
            audit_ref: null,
          },
        ]);
      }
      if (path === "/api/actions/action-1/decide" && init?.method === "POST") {
        return jsonResponse({ state: "approved" });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(screen.getByRole("navigation", { name: "제품 탐색" })).getByRole("button", { name: "판단" }));

    expect((await screen.findAllByText("업무 만들기")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "승인" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/actions/action-1/decide",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const request = fetchMock.mock.calls.find(([path]) => path === "/api/actions/action-1/decide");
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({ expected_version: 4, decision: "approve" });
  });

  it("shows an AX ActionItem without decision controls when action.decide is not granted", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/developer/personas") {
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
      if (path === "/api/developer/personas") {
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

    const details = await screen.findByText("내 업무 조회 · 완료");
    fireEvent.click(details);
    expect(await screen.findByText("현재 권한의 업무만 조회")).toBeTruthy();
    expect(screen.getByText("업무 2건")).toBeTruthy();
    expect(screen.getByText("321ms")).toBeTruthy();
  });

  it("restores an existing daily-report draft and submission history for the selected date", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/developer/personas") {
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
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/daily-reports/report-1/history",
      expect.anything(),
    );
  });

  it("clears a stale report error as soon as the selected report date changes", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/developer/personas") {
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
      if (path.startsWith("/api/daily-reports/status?report_date=2026-09-03")) {
        return new Response(JSON.stringify({ detail: "기존 날짜를 불러오지 못했습니다." }), { status: 500 });
      }
      if (path.startsWith("/api/daily-reports/status?report_date=2026-09-02")) {
        return new Promise(() => {});
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(screen.getByRole("navigation", { name: "제품 탐색" })).getByRole("button", { name: "보고" }));
    expect((await screen.findByRole("alert")).textContent).toContain("기존 날짜를 불러오지 못했습니다.");

    fireEvent.change(screen.getByLabelText("보고일"), { target: { value: "2026-09-02" } });
    await waitFor(() => {
      expect(screen.queryByRole("alert")).toBeNull();
    });
  });

  it("ignores a late same-id list snapshot after creating a Conversation", async () => {
    let resolveConversationList: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/developer/personas") {
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
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/conversations",
        expect.objectContaining({ method: "POST" }),
      );
      expect(resolveConversationList).toBeTruthy();
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "새 대화" }).getAttribute("aria-pressed")).toBe("true");
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

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "새 대화" }).getAttribute("aria-pressed")).toBe("true");
    });
    expect(screen.getByText("새 대화의 현재 발화")).toBeTruthy();
    expect(screen.getByText("실행 중")).toBeTruthy();
  });

  it("applies only the latest overlapping Conversation list response", async () => {
    const listResolvers: Array<(response: Response) => void> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/developer/personas") {
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

    listResolvers[1](
      jsonResponse([
        {
          conversation_id: "conversation-1",
          title: "최신 목록",
          version: 2,
          messages: [],
          turns: [],
          context_references: [],
          tool_invocations: [],
          actions: [],
        },
      ]),
    );
    expect(await screen.findByRole("button", { name: "최신 목록" })).toBeTruthy();

    listResolvers[0](
      jsonResponse([
        {
          conversation_id: "conversation-1",
          title: "오래된 목록",
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
      if (path === "/api/developer/personas") {
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
      if (path === "/api/conversations/conversation-1") {
        return new Promise<Response>((resolve) => detailResolvers.push(resolve));
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", withSession(fetchMock));

    render(<App />);
    await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    await waitFor(() => expect(detailResolvers).toHaveLength(2), { timeout: 2_500 });

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
      if (path === "/api/developer/personas") {
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
      expect(screen.getAllByText("최신 판단 카드").length).toBeGreaterThanOrEqual(2);
      expect(screen.getByText("내 업무 조회 · 완료")).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "교차 요청 확인" }));
    expect(screen.getByText("상세의 최신 상태")).toBeTruthy();
    expect(screen.queryByText("목록의 오래된 상태")).toBeNull();
    expect(screen.getAllByText("최신 판단 카드").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("내 업무 조회 · 완료")).toBeTruthy();
  });

  it("keeps existing AX sessions when a new Conversation is created", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/developer/personas") {
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
      if (path === "/api/conversations") {
        return jsonResponse([
          {
            conversation_id: "existing-conversation",
            title: "기존 대화",
            version: 1,
            messages: [],
            turns: [],
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
    expect(await screen.findByRole("button", { name: "기존 대화" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "새 AX 대화" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "기존 대화" })).toBeTruthy();
      expect(screen.getAllByRole("button", { name: "새 대화" }).some((button) => button.getAttribute("aria-pressed") === "true")).toBe(true);
    });
  });

  it("keeps an in-flight canonical Conversation list when creating a new session fails", async () => {
    let resolveConversationList: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/developer/personas") {
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
    expect((await screen.findByRole("alert")).textContent).toContain("대화를 만들지 못했습니다.");

    resolveConversationList?.(
      jsonResponse([
        {
          conversation_id: "existing-conversation",
          title: "기존 대화",
          version: 1,
          messages: [],
          turns: [],
          context_references: [],
          tool_invocations: [],
          actions: [],
        },
      ]),
    );
    expect(await screen.findByRole("button", { name: "기존 대화" })).toBeTruthy();
  });

  it("invalidates an old persona's Conversation list and clears its local AX state on a persona switch", async () => {
    let resolveMinaList: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const personaId = new Headers(init?.headers).get("X-Demo-Persona");
      if (path === "/api/developer/personas") {
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
    expect(await screen.findByText("민아의 현재 발화")).toBeTruthy();

    await switchAccount("jiho", "지호");
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
      if (path === "/api/developer/personas") return jsonResponse([{ id: "mina", display_name: "민아" }, { id: "jiho", display_name: "지호" }]);
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
    const conversationButton = await screen.findByRole("button", { name: "민아의 비공개 대화" });
    fireEvent.click(conversationButton);
    await waitFor(() => expect(resolveMinaDetail).toBeTruthy(), { timeout: 2_500 });
    await switchAccount("jiho", "지호");
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));
    await waitFor(() => expect(resolveJihoList).toBeTruthy());
    resolveJihoList?.(jsonResponse([{
      ...minaConversation,
      conversation_id: "jiho-conversation",
      title: "지호의 대화",
      turns: [],
    }]));
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
      if (path === "/api/developer/personas") {
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
      if (path === "/api/conversations") return jsonResponse([]);
      if (path === "/api/conversations/conversation-1") {
        conversationReads += 1;
        return jsonResponse(completedConversation);
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
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "새 대화" }).getAttribute("aria-pressed")).toBe("true");
    });
    expect(screen.getByText("실행 중")).toBeTruthy();
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
      if (path === "/api/developer/personas") {
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
    fireEvent.click(await screen.findByRole("button", { name: "AX" }));

    await screen.findByText("업무 확인");
    const context = await screen.findByLabelText("현재 화면 참고 자료");
    fireEvent.change(context, { target: { value: "task:task-1:3" } });
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
});
