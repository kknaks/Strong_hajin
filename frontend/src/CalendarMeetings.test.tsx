import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({
  getCalendarEntries: vi.fn(),
  getMyWork: vi.fn(),
  getTasks: vi.fn(),
  transitionDirectTask: vi.fn(),
  updateTask: vi.fn(),
  getMeeting: vi.fn(),
  createMeetingNote: vi.fn(),
  saveMeetingNote: vi.fn(),
  finalizeMeetingNote: vi.fn(),
  adoptMeetingSummary: vi.fn(),
  getTaskMaterials: vi.fn(),
  uploadTaskMaterial: vi.fn(),
  detachTaskMaterial: vi.fn(),
  taskMaterialContentUrl: () => "",
}));

import * as api from "./api";
import { CalendarPage } from "./CalendarPage";

function renderPage(entries: unknown[]) {
  vi.mocked(api.getMyWork).mockResolvedValue([]);
  vi.mocked(api.getTasks).mockResolvedValue([]);
  vi.mocked(api.getTaskMaterials).mockResolvedValue([]);
  vi.mocked(api.getCalendarEntries).mockResolvedValue(entries as never);
  render(
    <CalendarPage
      canManageOwnTasks
      onAskAboutTask={vi.fn()}
      onError={vi.fn()}
      onNotice={vi.fn()}
      personaId="mina"
      personaName="민아 (구성원)"
      personas={[]}
    />,
  );
}

const readable = {
  kind: "meeting",
  meeting_id: "m1",
  organization_id: "scax",
  owner_id: "mina",
  title: "주간 회의",
  starts_at: "2026-09-10T01:00:00Z",
  ends_at: "2026-09-10T02:00:00Z",
  visibility: "private",
  lifecycle: "scheduled",
  version: 1,
  attendees: [{ member_id: "jiho", display_name: "지호 (팀장)" }],
};

describe("calendar meeting list", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("switches between the calendar and the meeting list", async () => {
    renderPage([readable]);
    expect(await screen.findByRole("tab", { name: "캘린더 보기" })).toBeTruthy();
    expect(screen.queryByLabelText("회의 목록")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "회의 목록" }));
    const list = await screen.findByLabelText("회의 목록");
    expect(within(list).getByText("주간 회의")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "회의 목록" }).getAttribute("aria-selected")).toBe("true");
  });

  it("renders a concealed meeting as a bare busy block, never as a meeting with hidden fields", async () => {
    renderPage([readable, { kind: "busy", starts_at: "2026-09-10T05:00:00Z", ends_at: "2026-09-10T06:00:00Z" }]);
    fireEvent.click(await screen.findByRole("tab", { name: "회의 목록" }));
    const list = await screen.findByLabelText("회의 목록");
    const rows = list.querySelectorAll(".meeting-row");
    expect(rows).toHaveLength(2);

    const busy = list.querySelector(".meeting-row.busy") as HTMLElement;
    expect(within(busy).getByText("다른 일정")).toBeTruthy();
    expect(within(busy).getByText(/이 시간에 다른 일정이 있습니다/)).toBeTruthy();
    // Nothing to open, and no identity to read.
    expect(busy.getAttribute("data-meeting-id")).toBeNull();
    expect(within(busy).queryByRole("button")).toBeNull();
    expect(busy.textContent).not.toContain("주간 회의");
  });

  it("opens the meeting detail for a row the caller may read", async () => {
    vi.mocked(api.getMeeting).mockResolvedValue({ ...readable, note: null, recordings: [], summaries: [] } as never);
    renderPage([readable]);
    fireEvent.click(await screen.findByRole("tab", { name: "회의 목록" }));
    fireEvent.click(await screen.findByRole("button", { name: "상세보기" }));

    await waitFor(() => expect(api.getMeeting).toHaveBeenCalledWith("m1"));
    const drawer = await screen.findByRole("dialog", { name: "회의 상세" });
    expect(within(drawer).getByText("주간 회의")).toBeTruthy();
    expect(within(drawer).getByText("비공개")).toBeTruthy();
  });

  it("says so plainly when there is nothing on the calendar", async () => {
    renderPage([]);
    fireEvent.click(await screen.findByRole("tab", { name: "회의 목록" }));
    expect(await screen.findByText("회의가 없습니다")).toBeTruthy();
  });
});
