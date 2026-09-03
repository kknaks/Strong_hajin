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

      if (path === "/api/tasks/task-1/start" && init?.method === "POST") {
        taskState = "in_progress";
        return jsonResponse({ task_id: "task-1", state: taskState });
      }

      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("고객 피드백 정리")).toBeTruthy();
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
    fireEvent.click(within(navigation).getByRole("button", { name: "판단" }));
    expect(await screen.findByText("UI로 만든 업무 요청")).toBeTruthy();
    expect(screen.getByRole("button", { name: "수락" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "수락" }));

    fireEvent.click(within(navigation).getByRole("button", { name: "내 업무" }));
    expect(await screen.findByText("UI로 만든 업무 요청")).toBeTruthy();
  });
});
