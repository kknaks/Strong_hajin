import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DailyReportPage } from "./DailyReportPage";
import { seoulToday } from "./labels";


function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
    status: 200,
  });
}


describe("DailyReportPage", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("restores an accepted generation and polls the same job until its draft is ready", async () => {
    let statusCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/tasks" || path === "/api/my-work") return jsonResponse([]);
      if (path.startsWith("/api/daily-reports/status")) {
        statusCalls += 1;
        if (statusCalls === 1) {
          return jsonResponse({
            report_date: seoulToday(),
            status: "not_started",
            report_id: null,
            generation_id: "generation-1",
            generation_status: "queued",
            generation_error_code: null,
          });
        }
        return jsonResponse({
          report_date: seoulToday(),
          status: "draft",
          report_id: "report-1",
          generation_id: "generation-1",
          generation_status: "completed",
          generation_error_code: null,
        });
      }
      if (path === "/api/daily-reports/report-1/history") {
        return jsonResponse({
          report_id: "report-1",
          report_date: seoulToday(),
          status: "draft",
          drafts: [{
            draft_id: "draft-1",
            version: 1,
            body: "복원된 비동기 보고 초안",
            source_refs: [],
            workflow_run_id: "run-1",
            definition_version_id: "definition-1",
          }],
          submissions: [],
        });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <DailyReportPage
        personaId="mina"
        personaName="민아"
        onError={vi.fn()}
      />,
    );

    expect((await screen.findByRole("button", { name: "초안 생성 중…" }) as HTMLButtonElement).disabled).toBe(true);
    expect((await screen.findByLabelText("일일보고 초안", {}, { timeout: 2500 }) as HTMLTextAreaElement).value)
      .toBe("복원된 비동기 보고 초안");
    expect(statusCalls).toBeGreaterThanOrEqual(2);
    expect(screen.getByRole("status").textContent).toContain("초안이 준비되었습니다");
  });

  it("keeps an uncertain provider result blocked after re-entry", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/tasks" || path === "/api/my-work") return jsonResponse([]);
      if (path.startsWith("/api/daily-reports/status")) {
        return jsonResponse({
          report_date: seoulToday(),
          status: "not_started",
          report_id: null,
          generation_id: "generation-uncertain",
          generation_status: "needs_verification",
          generation_error_code: "provider_result_uncertain",
        });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<DailyReportPage personaId="mina" personaName="민아" onError={vi.fn()} />);

    expect((await screen.findByRole("button", { name: "생성 결과 확인 필요" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toContain("새 요청을 보내기 전에 운영 기록을 확인");
    expect(fetchMock.mock.calls.filter(([input]) => String(input).startsWith("/api/daily-reports/status"))).toHaveLength(1);
  });
});
