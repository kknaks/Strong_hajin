import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const jsonResponse = (body: unknown) =>
  new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
  });

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
          capabilities: ["daily_report.generate"],
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
    vi.stubGlobal("fetch", fetchMock);

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
          capabilities: personaId === "mina" ? ["daily_report.generate"] : [],
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
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(navigation).getByRole("button", { name: "내 업무" }));
    await screen.findByLabelText("담당 후보");
    fireEvent.change(screen.getByLabelText("요청할 업무"), { target: { value: "UI로 만든 업무 요청" } });
    fireEvent.click(screen.getByRole("button", { name: "업무 요청 보내기" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/work-requests",
        expect.objectContaining({ method: "POST" }),
      );
    });

    fireEvent.change(screen.getByLabelText("사용자"), { target: { value: "jiho" } });
    await waitFor(() => {
      expect(within(navigation).queryByRole("button", { name: "보고" })).toBeNull();
    });
    fireEvent.click(within(navigation).getByRole("button", { name: "판단" }));
    expect(await screen.findByText("UI로 만든 업무 요청")).toBeTruthy();
    expect(screen.getByRole("button", { name: "수락" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "수락" }));

    fireEvent.click(within(navigation).getByRole("button", { name: "내 업무" }));
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
          capabilities: ["daily_report.generate"],
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
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(navigation).getByRole("button", { name: "판단" }));

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
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    const navigation = await screen.findByRole("navigation", { name: "제품 탐색" });
    fireEvent.click(within(navigation).getByRole("button", { name: "내 업무" }));
    fireEvent.click(screen.getByRole("button", { name: "AX" }));

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
