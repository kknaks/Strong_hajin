import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DailyReportPage } from "./DailyReportPage";
import { seoulToday } from "../../lib/labels";
import { scopeScreenCache } from "../../lib/screenCache";


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

/* WORK-008 Phase 2 (B-01 · fix1) — 탭 재진입. 탭 이동 = 언마운트 → 다시 마운트, 주인은 App 경로처럼 세운다. */
describe("DailyReportPage — 탭 재진입 (B-01)", () => {
  afterEach(() => {
    scopeScreenCache(null);
    cleanup();
    vi.unstubAllGlobals();
  });

  function reportApi(body: string, generation: string | null = "completed") {
    return async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/tasks" || path === "/api/my-work") return jsonResponse([]);
      if (path.startsWith("/api/daily-reports/status")) {
        return jsonResponse({ report_date: seoulToday(), status: "draft", report_id: "report-1", generation_id: "g-1", generation_status: generation, generation_error_code: null });
      }
      if (path === "/api/daily-reports/report-1/history") {
        return jsonResponse({
          report_id: "report-1",
          report_date: seoulToday(),
          status: "draft",
          drafts: [{ draft_id: "draft-1", version: 1, body, source_refs: [], workflow_run_id: "run-1", definition_version_id: "d-1" }],
          submissions: [],
        });
      }
      return new Response("not found", { status: 404 });
    };
  }
  const editor = () => screen.getByLabelText("일일보고 초안") as HTMLTextAreaElement;

  it("다시 들어오면 받아 둔 초안이 바로 서고, 갱신 응답 전에는 제출을 열지 않으며, 뒤에서 받은 초안으로 바뀐다", async () => {
    scopeScreenCache("mina");
    vi.stubGlobal("fetch", vi.fn(reportApi("어제 받은 초안")));
    const first = render(<DailyReportPage onError={vi.fn()} personaId="mina" personaName="민아" />);
    expect((await screen.findByLabelText("일일보고 초안") as HTMLTextAreaElement).value).toBe("어제 받은 초안");
    first.unmount();

    let release!: () => void;
    const hold = new Promise<void>((done) => (release = done));
    const later = reportApi("다시 읽은 초안");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      await hold;
      return later(input);
    }));
    render(<DailyReportPage onError={vi.fn()} personaId="mina" personaName="민아" />);
    expect(editor().value).toBe("어제 받은 초안");
    // 기억한 회차로 명령을 보내지 않는다 (WARN-1).
    expect((screen.getByRole("button", { name: "보고 제출" }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => release());
    await waitFor(() => expect(editor().value).toBe("다시 읽은 초안"));
    await waitFor(() => expect((screen.getByRole("button", { name: "보고 제출" }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("「생성 중」은 기억에서 되살리지 않는다 — 갱신 응답이 다시 세울 때만 선다 (WARN-5)", async () => {
    scopeScreenCache("mina");
    vi.stubGlobal("fetch", vi.fn(reportApi("생성하던 초안", "running")));
    const first = render(<DailyReportPage onError={vi.fn()} personaId="mina" personaName="민아" />);
    expect(await screen.findByRole("button", { name: "초안 생성 중…" })).toBeTruthy();
    first.unmount();

    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    render(<DailyReportPage onError={vi.fn()} personaId="mina" personaName="민아" />);
    expect(editor().value).toBe("생성하던 초안");
    expect(screen.queryByRole("button", { name: "초안 생성 중…" })).toBeNull();
  });
});
