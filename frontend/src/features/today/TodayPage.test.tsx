import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { scopeScreenCache } from "../../lib/screenCache";
import { TodayPage } from "./TodayPage";

/* WORK-008 Phase 2 (B-01 · fix1) — 홈의 탭 재진입.
   탭 이동 = 언마운트 → 다시 마운트. 기억의 주인은 `App` 이 세우므로 여기서는 App 경로처럼 직접 세운다. */

const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" }, status: 200 });

const judgement = (subject: string) => ({
  action_item_id: "ai-1",
  kind: "ax.task.create_self",
  operation_label: "AX 제안",
  status: "awaiting_review",
  subject,
  current_question: "등록할까요?",
  submission_version: 1,
  waiting_on: null,
  allowed_commands: ["confirm"],
});

function homeApi(subject: string, taskTitle: string) {
  return async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/my-work") return json([{ task_id: "t-1", title: taskTitle, state: "open", version: 1, block_reason: null }]);
    if (path === "/api/action-items") return json([judgement(subject)]);
    if (path.startsWith("/api/work-requests")) return json([]);
    if (path.endsWith("-candidates")) return json([]);
    return new Response("not found", { status: 404 });
  };
}

const noop = vi.fn();
const props = {
  personaId: "mina",
  personaName: "민아 (구성원)",
  personas: [],
  canReadActions: true,
  canDecideWorkRequests: false,
  canManageOwnTasks: true,
  canCreateWorkRequests: false,
  canGenerateDailyReport: false,
  onAskAx: noop,
  onAskAboutTask: noop,
  onNotice: noop,
  onDecided: async () => true,
  onError: noop,
  onNavigate: noop,
};

describe("홈 — 탭 재진입 (B-01)", () => {
  afterEach(() => {
    scopeScreenCache(null);
    cleanup();
    vi.unstubAllGlobals();
  });

  it("다시 들어오면 판단 카드·오늘의 업무가 빈 칸 없이 바로 서고, 갱신 전엔 판단·시작을 열지 않으며, 뒤에서 받은 값으로 바뀐다", async () => {
    scopeScreenCache("mina");
    vi.stubGlobal("fetch", vi.fn(homeApi("어제의 AX 제안", "어제의 업무")));
    const first = render(<TodayPage {...props} />);
    expect(await screen.findByText("어제의 AX 제안")).toBeTruthy();
    expect(await screen.findByText("어제의 업무")).toBeTruthy();
    first.unmount();

    let release!: () => void;
    const hold = new Promise<void>((done) => (release = done));
    const later = homeApi("오늘의 AX 제안", "오늘 받은 업무");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      await hold;
      return later(input);
    }));
    render(<TodayPage {...props} />);
    expect(screen.getByText("어제의 AX 제안")).toBeTruthy();
    expect(screen.getByText("어제의 업무")).toBeTruthy();
    expect(screen.queryByText("요청된 업무가 없습니다")).toBeNull();
    // 기억한 envelope 로 그린 명령은 갱신 응답 전까지 잠긴다 (WARN-1).
    expect((screen.getByRole("button", { name: "판단하기" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "시작" }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => release());
    expect(await screen.findByText("오늘의 AX 제안")).toBeTruthy();
    expect(await screen.findByText("오늘 받은 업무")).toBeTruthy();
    await waitFor(() => expect((screen.getByRole("button", { name: "판단하기" }) as HTMLButtonElement).disabled).toBe(false));
    expect((screen.getByRole("button", { name: "시작" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("진입 갱신이 실패해 잠긴 채여도, 셸의 다시 읽기(refresh)가 성공하면 판단·시작이 풀린다 (fix2 · WARN-A)", async () => {
    scopeScreenCache("mina");
    vi.stubGlobal("fetch", vi.fn(homeApi("어제의 AX 제안", "어제의 업무")));
    const first = render(<TodayPage {...props} />);
    expect(await screen.findByText("어제의 AX 제안")).toBeTruthy();
    first.unmount();

    // 다시 들어온 순간 서버가 끊겨 있다 — 진입 갱신이 실패한다.
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "down" }), { status: 503 })));
    let refresh: (() => Promise<void>) | null = null;
    render(<TodayPage {...props} onRegisterRefresh={(next) => (refresh = next ?? refresh)} />);
    await waitFor(() => expect(noop).toHaveBeenCalledWith(expect.any(String)));
    expect((screen.getByRole("button", { name: "판단하기" }) as HTMLButtonElement).disabled).toBe(true);

    // 서버가 돌아오고 셸이 다시 읽는다 — 탭을 나갔다 오지 않아도 풀린다.
    vi.stubGlobal("fetch", vi.fn(homeApi("다시 읽은 AX 제안", "다시 읽은 업무")));
    await act(async () => {
      await refresh?.();
    });
    expect(screen.getByText("다시 읽은 AX 제안")).toBeTruthy();
    expect((screen.getByRole("button", { name: "판단하기" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "시작" }) as HTMLButtonElement).disabled).toBe(false);
  });

  /* WORK-008 A-01 · SPEC-002 §2.4 — 홈 판단 대기의 AX 업무 초안을 누르면 채팅과 같은 요약 카드가 뜬다. */
  it("AX 업무 초안 카드를 누르면 같은 요약 카드(모달)가 뜬다", async () => {
    const draft = {
      ...judgement("AX 가 만든 초안"),
      kind: "ax.task.create_self",
      submission_version: 1,
      expected_version: 3,
      created_at: "2026-09-30T01:00:00+00:00",
      allowed_commands: [{ id: "confirm", label: "등록", tone: "primary" }],
      edit_contract: {
        editor: "task",
        base_submission_version: 1,
        values: { title: "AX 가 만든 초안", due_date: null, checklist: [] },
        fields: [{ id: "title", label: "제목", type: "text", required: true, editable: true }],
      },
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/action-items") return json([draft]);
      return homeApi("x", "y")(input);
    }));
    render(<TodayPage {...props} />);
    fireEvent.click(await screen.findByRole("button", { name: "판단하기" }));
    const modal = await screen.findByRole("dialog", { name: "AX 제안" });
    expect(within(modal).getByRole("region", { name: /AX 업무 생성/ })).toBeTruthy();
    expect(within(modal).getAllByRole("tab")).toHaveLength(4);
    expect(within(modal).getByRole("button", { name: "등록" })).toBeTruthy();
  });
});
