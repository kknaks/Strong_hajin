import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MeetingDetail } from "./viewModels";

vi.mock("./api", () => ({
  getMeeting: vi.fn(),
  createMeetingNote: vi.fn(),
  saveMeetingNote: vi.fn(),
  finalizeMeetingNote: vi.fn(),
  adoptMeetingSummary: vi.fn(),
  promoteMeetingFollowup: vi.fn(),
}));

import * as api from "./api";
import { MeetingDrawer } from "./MeetingDrawer";

const rawSegment = (id: string, start: number, text: string) => ({
  segment_id: id,
  source_segment_key: `provider-${id}`,
  start_ms: start,
  end_ms: start + 1_500,
  text,
  speaker_label: "Speaker 1",
  confirmed_member_id: null,
});

const meeting = (overrides: Partial<MeetingDetail> = {}): MeetingDetail => ({
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
  attendees: [
    { member_id: "mina", display_name: "민아 (구성원)" },
    { member_id: "jiho", display_name: "지호 (팀장)" },
  ],
  note: null,
  recordings: [],
  summaries: [],
  ...overrides,
});

const recordedMeeting = () =>
  meeting({
    recordings: [
      {
        recording_id: "r1",
        meeting_id: "m1",
        purpose: "회의록",
        state: "transcribed",
        version: 2,
        content_type: "audio/webm",
        original_name: "raw.webm",
        size_bytes: 100,
        sha256: "abc",
        started_at: "2026-09-10T01:00:00Z",
        ended_at: "2026-09-10T02:00:00Z",
        storage_key: null,
        raw_transcript: {
          transcript_revision_id: "t1",
          revision: 1,
          state: "final",
          provider: "soniox",
          segments: [rawSegment("raw-1", 0, "안녕 하세요"), rawSegment("raw-2", 1_500, "일정을 논의 합니다")],
        },
        refinement: {
          refinement_revision_id: "f1",
          raw_transcript_revision_id: "t1",
          revision: 1,
          state: "completed",
          segments: [
            { ...rawSegment("ref-1", 0, "안녕하세요. 일정을 논의합니다."), raw_start_segment_id: "raw-1", raw_end_segment_id: "raw-2", correction_kind: "merge", confidence: 0.9 },
          ],
        },
        speaker_assignments: [],
      },
    ],
    summaries: [
      {
        summary_id: "s1",
        meeting_id: "m1",
        raw_transcript_revision_id: "t1",
        refinement_revision_id: "f1",
        kind: "final",
        state: "completed",
        version: 1,
        body: "일정을 논의했습니다.",
        evidence: [
          {
            statement_index: 1,
            kind: "summary",
            text: "일정을 논의했습니다.",
            refinement_start_segment_id: "ref-1",
            refinement_end_segment_id: "ref-1",
            raw_start_segment_id: "raw-1",
            raw_end_segment_id: "raw-2",
            raw_start_ms: 0,
            raw_end_ms: 3_000,
          },
        ],
        statements: [
          { statement_index: 1, kind: "summary", text: "일정을 논의했습니다.", raw_start_ms: 0, raw_end_ms: 3_000, promoted: false },
          { statement_index: 2, kind: "followup", text: "지호가 계약서를 검토한다", raw_start_ms: 0, raw_end_ms: 3_000, promoted: false },
        ],
      },
    ],
  });

function renderDrawer(detail: MeetingDetail) {
  vi.mocked(api.getMeeting).mockResolvedValue(detail);
  const onError = vi.fn();
  const onNotice = vi.fn();
  const onChanged = vi.fn().mockResolvedValue(undefined);
  render(<MeetingDrawer meetingId={detail.meeting_id} onChanged={onChanged} onClose={vi.fn()} onError={onError} onNotice={onNotice} personaId="mina" />);
  return { onError, onNotice, onChanged };
}

describe("회의에서 나온 후속 업무", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("offers to make a candidate into work, and says so once someone did", async () => {
    vi.mocked(api.promoteMeetingFollowup).mockResolvedValue({
      already_promoted: false,
      task: { task_id: "task-9", title: "지호가 계약서를 검토한다" },
      work_request: null,
    } as never);
    const { onNotice } = renderDrawer(recordedMeeting());
    const candidates = await screen.findByLabelText("후속 업무 후보");
    const row = within(candidates).getByRole("listitem");
    expect(row.textContent).toContain("지호가 계약서를 검토한다");
    // A decision is a decision, not a summary line: only followups are offered.
    expect(within(candidates).getAllByRole("listitem")).toHaveLength(1);

    vi.mocked(api.getMeeting).mockResolvedValue({
      ...recordedMeeting(),
      summaries: [
        {
          ...recordedMeeting().summaries[0],
          statements: [
            { statement_index: 1, kind: "summary", text: "일정을 논의했습니다.", raw_start_ms: 0, raw_end_ms: 3_000, promoted: false },
            { statement_index: 2, kind: "followup", text: "지호가 계약서를 검토한다", raw_start_ms: 0, raw_end_ms: 3_000, promoted: true, promoted_task_id: "task-9" },
          ],
        },
      ],
    } as never);
    fireEvent.click(within(row).getByRole("button", { name: "내 업무로 만들기" }));

    await waitFor(() =>
      expect(api.promoteMeetingFollowup).toHaveBeenCalledWith("m1", "s1", 2, { kind: "task", title: "지호가 계약서를 검토한다" }),
    );
    await waitFor(() => expect(onNotice).toHaveBeenCalled());
    // Once it is work, the candidate stops offering to make it again and points at the work instead.
    const after = within(await screen.findByLabelText("후속 업무 후보")).getByRole("listitem");
    expect(within(after).getByRole("button", { name: "만든 업무 열기" })).toBeTruthy();
    expect(within(after).queryByRole("button", { name: "내 업무로 만들기" })).toBeNull();
  });
});

describe("meeting note lifecycle", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("distinguishes no note, saving and saved rather than showing one silent state", async () => {
    let release: null | ((value: unknown) => void) = null;
    vi.mocked(api.createMeetingNote).mockImplementation(
      () => new Promise((resolve) => {
        release = () => resolve({ note_id: "n1", lifecycle: "draft", version: 1, body: "결정 사항", versions: [], finalized_at: null, finalized_by: null } as never);
      }),
    );
    renderDrawer(meeting());
    const section = await screen.findByLabelText("회의록");
    expect(within(section).getByText("· 아직 없음")).toBeTruthy();

    fireEvent.change(within(section).getByLabelText("회의록 내용"), { target: { value: "결정 사항" } });
    fireEvent.click(screen.getByRole("button", { name: "회의록 작성" }));
    await waitFor(() => expect(within(section).getByText("저장 중…")).toBeTruthy());

    (release as unknown as (value: unknown) => void)(undefined);
    await waitFor(() => expect(within(section).getByText("저장됨")).toBeTruthy());
    expect(within(section).getByText("· v1")).toBeTruthy();
  });

  it("tells a version conflict apart from a failed write and offers the move that fits each", async () => {
    const note = { note_id: "n1", lifecycle: "draft", version: 3, body: "이전 내용", versions: [], finalized_at: null, finalized_by: null };
    vi.mocked(api.saveMeetingNote).mockRejectedValueOnce(new Error("meeting note version is stale"));
    renderDrawer(meeting({ note: note as never }));
    const section = await screen.findByLabelText("회의록");
    fireEvent.change(within(section).getByLabelText("회의록 내용"), { target: { value: "내가 쓴 내용" } });
    fireEvent.click(screen.getByRole("button", { name: "회의록 저장" }));

    await waitFor(() => expect(within(section).getByText(/다른 곳에서 먼저 저장/)).toBeTruthy());
    expect(within(section).getByRole("button", { name: "최신 내용 불러오기" })).toBeTruthy();
    expect(within(section).queryByRole("button", { name: "다시 저장" })).toBeNull();

    // A transport failure is a different situation: the same text should just be sent again.
    vi.mocked(api.saveMeetingNote).mockRejectedValueOnce(new Error("네트워크 오류"));
    fireEvent.change(within(section).getByLabelText("회의록 내용"), { target: { value: "다시 쓴 내용" } });
    fireEvent.click(screen.getByRole("button", { name: "회의록 저장" }));
    await waitFor(() => expect(within(section).getByText("저장하지 못했습니다.")).toBeTruthy());
    expect(within(section).getByRole("button", { name: "다시 저장" })).toBeTruthy();
  });
});

describe("meeting transcript and summary", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("reads the refined transcript by default and keeps the immutable original one toggle away", async () => {
    renderDrawer(recordedMeeting());
    const section = await screen.findByLabelText("녹음");
    expect(within(section).getByText("전사 완료")).toBeTruthy();
    // Refined first: it is what a person can follow.
    expect(within(section).getByText("안녕하세요. 일정을 논의합니다.")).toBeTruthy();
    expect(within(section).queryByText("안녕 하세요")).toBeNull();
    expect(within(section).getByRole("button", { name: "정제 대화록" }).getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(within(section).getByRole("button", { name: "원본 STT 보기" }));
    // The provider's own output, unedited.
    expect(within(section).getByText("안녕 하세요")).toBeTruthy();
    expect(within(section).getByText("일정을 논의 합니다")).toBeTruthy();
    expect(within(section).queryByText("안녕하세요. 일정을 논의합니다.")).toBeNull();
  });

  it("falls back to the original when refinement never produced a version", async () => {
    const detail = recordedMeeting();
    detail.recordings[0].refinement = null;
    renderDrawer(detail);
    const section = await screen.findByLabelText("녹음");
    expect(within(section).getByText("안녕 하세요")).toBeTruthy();
    expect(within(section).getByText(/정제본이 없어 원본을 보여 줍니다/)).toBeTruthy();
    expect((within(section).getByRole("button", { name: "정제 대화록" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("says a failed transcription plainly and still lets the note be written", async () => {
    const detail = recordedMeeting();
    detail.recordings[0].state = "failed";
    detail.recordings[0].raw_transcript = null;
    detail.recordings[0].refinement = null;
    renderDrawer(detail);
    const section = await screen.findByLabelText("녹음");
    expect(within(section).getByText("실패")).toBeTruthy();
    expect(within(section).getByText(/회의록은 그대로 편집할 수 있습니다/)).toBeTruthy();
    expect((await screen.findByLabelText("회의록 내용") as HTMLTextAreaElement).disabled).toBe(false);
  });

  it("jumps from a summary statement to the segment it cites, in whichever transcript is showing", async () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    renderDrawer(recordedMeeting());
    const summarySection = await screen.findByLabelText("AI 요약");
    fireEvent.click(within(summarySection).getByRole("button", { name: "근거 00:00" }));

    const transcript = screen.getByLabelText("녹음");
    await waitFor(() => expect(transcript.querySelector('[data-segment-id="ref-1"]')?.className).toContain("marked"));
    expect(scrollIntoView).toHaveBeenCalled();

    // Reading the original instead points the same statement at its raw segment.
    fireEvent.click(within(transcript).getByRole("button", { name: "원본 STT 보기" }));
    fireEvent.click(within(summarySection).getByRole("button", { name: "근거 00:00" }));
    await waitFor(() => expect(transcript.querySelector('[data-segment-id="raw-1"]')?.className).toContain("marked"));
  });

  it("adopts a summary as a new note version instead of overwriting what a person wrote", async () => {
    const adopted = { ...recordedMeeting(), note: { note_id: "n1", lifecycle: "draft", version: 2, body: "일정을 논의했습니다.", versions: [], finalized_at: null, finalized_by: null } };
    vi.mocked(api.adoptMeetingSummary).mockResolvedValue({} as never);
    vi.mocked(api.getMeeting).mockResolvedValueOnce(recordedMeeting()).mockResolvedValueOnce(adopted as never);
    const onNotice = vi.fn();
    render(<MeetingDrawer meetingId="m1" onChanged={vi.fn()} onClose={vi.fn()} onError={vi.fn()} onNotice={onNotice} personaId="mina" />);

    const summarySection = await screen.findByLabelText("AI 요약");
    fireEvent.click(within(summarySection).getByRole("button", { name: "회의록으로 채택" }));
    await waitFor(() => expect(api.adoptMeetingSummary).toHaveBeenCalledWith("m1", "s1", 1));
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("AI 요약을 회의록 v2로 채택했습니다."));
  });
});
