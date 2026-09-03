import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const jsonResponse = (body: unknown) =>
  new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
  });

describe("product surfaces", () => {
  afterEach(() => {
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
});
