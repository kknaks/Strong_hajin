import { useEffect, useMemo, useRef, useState } from "react";

import { adoptMeetingSummary, createMeetingNote, finalizeMeetingNote, getMeeting, saveMeetingNote } from "./api";
import { formatDateTime, personName } from "./labels";
import { Drawer } from "./Modal";
import type { MeetingDetail, MeetingRecording, MeetingSummary, RawTranscriptSegment, RefinedTranscriptSegment } from "./viewModels";

/**
 * One meeting's whole record, read through a single authorized projection.
 *
 * The layering the server keeps is the layering shown: the refined transcript reads by default because it is what a
 * person can actually follow, the immutable provider output stays one toggle away, and a summary statement points at
 * the segment it came from. Nothing here re-derives a permission — a meeting the caller may not read never arrives.
 */

const recordingStateLabel: Record<string, string> = {
  not_started: "시작 전",
  recording: "녹음 중",
  uploaded: "업로드됨",
  transcribing: "전사 중",
  transcribed: "전사 완료",
  failed: "실패",
  deleted: "삭제됨",
};

const noteStatusLabel: Record<string, string> = {
  idle: "",
  saving: "저장 중…",
  saved: "저장됨",
  conflict: "다른 곳에서 먼저 저장되었습니다. 최신 내용을 불러온 뒤 다시 저장하세요.",
  failed: "저장하지 못했습니다.",
};

function offset(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function speakerOf(segment: RawTranscriptSegment, names: Map<string, string>): string {
  if (segment.confirmed_member_id) return personName(names.get(segment.confirmed_member_id) ?? segment.confirmed_member_id);
  return segment.speaker_label ?? "화자 미상";
}

function TranscriptView({
  recording,
  names,
  showRaw,
  highlighted,
  registerSegment,
}: {
  recording: MeetingRecording;
  names: Map<string, string>;
  showRaw: boolean;
  highlighted: string | null;
  registerSegment: (id: string, node: HTMLLIElement | null) => void;
}) {
  const refined = recording.refinement?.segments ?? [];
  const raw = recording.raw_transcript?.segments ?? [];
  const segments: Array<RawTranscriptSegment | RefinedTranscriptSegment> = showRaw ? raw : refined.length > 0 ? refined : raw;
  if (segments.length === 0) return <p className="t-meta">아직 대화록이 없습니다.</p>;
  return (
    <ol className="transcript">
      {segments.map((segment) => {
        const marked = highlighted === segment.segment_id;
        return (
          <li
            className={marked ? "transcript-line marked" : "transcript-line"}
            data-segment-id={segment.segment_id}
            key={segment.segment_id}
            ref={(node) => registerSegment(segment.segment_id, node)}
          >
            <span className="transcript-meta">
              <b>{speakerOf(segment, names)}</b>
              <span className="t-meta">{offset(segment.start_ms)}</span>
            </span>
            <span className="transcript-text">{segment.text}</span>
            {"correction_kind" in segment && segment.correction_kind && (
              <span className="transcript-correction" title={`정제: ${segment.correction_kind}`}>
                {segment.correction_kind}
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

export function MeetingDrawer({
  meetingId,
  personaId,
  onClose,
  onChanged,
  onError,
  onNotice,
}: {
  meetingId: string;
  personaId: string;
  onClose: () => void;
  onChanged: () => Promise<void> | void;
  onError: (message: string | null) => void;
  onNotice?: (message: string) => void;
}) {
  const [meeting, setMeeting] = useState<MeetingDetail | null>(null);
  const [body, setBody] = useState("");
  const [noteStatus, setNoteStatus] = useState<keyof typeof noteStatusLabel>("idle");
  const [showRaw, setShowRaw] = useState(false);
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const segmentNodes = useRef(new Map<string, HTMLLIElement>());

  const load = async () => {
    const detail = await getMeeting(meetingId);
    setMeeting(detail);
    setBody(detail.note?.body ?? "");
    return detail;
  };

  useEffect(() => {
    let cancelled = false;
    void getMeeting(meetingId)
      .then((detail) => {
        if (cancelled) return;
        setMeeting(detail);
        setBody(detail.note?.body ?? "");
      })
      .catch((error: unknown) => {
        if (!cancelled) onError(error instanceof Error ? error.message : "회의를 불러오지 못했습니다.");
      });
    return () => {
      cancelled = true;
    };
  }, [meetingId, onError]);

  const names = useMemo(
    () => new Map((meeting?.attendees ?? []).map((attendee) => [attendee.member_id, attendee.display_name])),
    [meeting],
  );
  const recording = meeting?.recordings?.[0] ?? null;
  const dirty = meeting !== null && body !== (meeting.note?.body ?? "");

  async function saveNote() {
    if (!meeting || busy) return;
    setBusy(true);
    setNoteStatus("saving");
    onError(null);
    try {
      const note = meeting.note
        ? await saveMeetingNote(meeting.meeting_id, meeting.note.version, body)
        : await createMeetingNote(meeting.meeting_id, body);
      setMeeting({ ...meeting, note });
      setBody(note.body);
      setNoteStatus("saved");
      await onChanged();
    } catch (error) {
      const message = error instanceof Error ? error.message : "";
      // A stale version is a different problem from a failed write, and needs a different move from the person.
      setNoteStatus(/version|stale|충돌/i.test(message) ? "conflict" : "failed");
      onError(message || "회의록을 저장하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function finalize() {
    if (!meeting?.note || busy) return;
    setBusy(true);
    onError(null);
    try {
      const note = await finalizeMeetingNote(meeting.meeting_id, meeting.note.version);
      setMeeting({ ...meeting, note });
      onNotice?.("회의록을 확정했습니다.");
      await onChanged();
    } catch (error) {
      onError(error instanceof Error ? error.message : "회의록을 확정하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function adopt(summary: MeetingSummary) {
    if (!meeting || busy) return;
    setBusy(true);
    onError(null);
    try {
      await adoptMeetingSummary(meeting.meeting_id, summary.summary_id, summary.version);
      const detail = await load();
      onNotice?.(`AI 요약을 회의록 v${detail.note?.version ?? 1}로 채택했습니다.`);
      await onChanged();
    } catch (error) {
      onError(error instanceof Error ? error.message : "요약을 채택하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  /** Jumping from a statement to its evidence: the refined segment it cites, or the raw one when reading the original. */
  function showEvidence(summary: MeetingSummary, index: number) {
    const evidence = summary.evidence[index];
    if (!evidence) return;
    const target = showRaw ? evidence.raw_start_segment_id : evidence.refinement_start_segment_id;
    setHighlighted(target);
    segmentNodes.current.get(target)?.scrollIntoView({ block: "center" });
  }

  return (
    <Drawer
      footer={
        <>
          <button className="btn h40 ghost" onClick={onClose} type="button">
            닫기
          </button>
          <span className="spacer" />
          {meeting?.note && meeting.note.lifecycle !== "finalized" && (
            <button className="btn h40" disabled={busy || dirty} onClick={() => void finalize()} type="button">
              회의록 확정
            </button>
          )}
          <button className="btn h40 primary" disabled={busy || !dirty || !body.trim()} onClick={() => void saveNote()} type="button">
            {meeting?.note ? "회의록 저장" : "회의록 작성"}
          </button>
        </>
      }
      kicker="회의"
      label="회의 상세"
      onClose={onClose}
      title={meeting?.title ?? "불러오는 중…"}
    >
      {!meeting ? (
        <p className="t-meta">회의를 불러오는 중…</p>
      ) : (
        <>
          <dl className="drawer-facts">
            <div>
              <dt>시간</dt>
              <dd>
                {formatDateTime(meeting.starts_at)} – {formatDateTime(meeting.ends_at).slice(-5)}
              </dd>
            </div>
            <div>
              <dt>공개 범위</dt>
              <dd>{meeting.visibility === "public" ? "조직 공개" : "비공개"}</dd>
            </div>
            <div>
              <dt>참석자</dt>
              <dd>{meeting.attendees.map((attendee) => personName(attendee.display_name)).join(", ") || "없음"}</dd>
            </div>
          </dl>

          <section aria-label="회의록" className="drawer-section">
            <h4>
              회의록{" "}
              {meeting.note ? (
                <small className="t-meta">
                  · v{meeting.note.version}
                  {meeting.note.lifecycle === "finalized" ? " · 확정됨" : ""}
                </small>
              ) : (
                <small className="t-meta">· 아직 없음</small>
              )}
            </h4>
            <label className="sr-only" htmlFor="meeting-note-body">
              회의록 내용
            </label>
            <textarea
              disabled={busy || meeting.note?.lifecycle === "finalized"}
              id="meeting-note-body"
              onChange={(event) => {
                setBody(event.target.value);
                setNoteStatus("idle");
              }}
              placeholder="논의한 내용과 결정을 적어 두세요. AI 요약은 이 내용을 덮어쓰지 않고 새 버전으로만 들어옵니다."
              rows={5}
              value={body}
            />
            {noteStatus !== "idle" && (
              <p className={noteStatus === "conflict" || noteStatus === "failed" ? "note-status danger-text" : "note-status t-meta"} role="status">
                {noteStatusLabel[noteStatus]}
                {noteStatus === "conflict" && (
                  <button className="btn h30" onClick={() => void load()} type="button">
                    최신 내용 불러오기
                  </button>
                )}
                {noteStatus === "failed" && (
                  <button className="btn h30" onClick={() => void saveNote()} type="button">
                    다시 저장
                  </button>
                )}
              </p>
            )}
          </section>

          <section aria-label="녹음" className="drawer-section">
            <h4>녹음과 전사</h4>
            {!recording ? (
              <p className="t-meta">아직 녹음이 없습니다.</p>
            ) : (
              <>
                <p className="recording-state" data-state={recording.state}>
                  <b>{recordingStateLabel[recording.state] ?? recording.state}</b>
                  {recording.state === "failed" && <span className="danger-text"> · 전사에 실패했습니다. 회의록은 그대로 편집할 수 있습니다.</span>}
                  {recording.ended_at && <span className="t-meta"> · {formatDateTime(recording.ended_at)}</span>}
                </p>
                {(recording.refinement || recording.raw_transcript) && (
                  <div className="transcript-toolbar">
                    <button
                      aria-pressed={!showRaw}
                      className={showRaw ? "btn h30" : "btn h30 primary"}
                      disabled={!recording.refinement}
                      onClick={() => setShowRaw(false)}
                      type="button"
                    >
                      정제 대화록
                    </button>
                    <button aria-pressed={showRaw} className={showRaw ? "btn h30 primary" : "btn h30"} onClick={() => setShowRaw(true)} type="button">
                      원본 STT 보기
                    </button>
                    {!recording.refinement && <span className="t-meta">정제본이 없어 원본을 보여 줍니다.</span>}
                  </div>
                )}
                <TranscriptView
                  highlighted={highlighted}
                  names={names}
                  recording={recording}
                  registerSegment={(id, node) => {
                    if (node) segmentNodes.current.set(id, node);
                    else segmentNodes.current.delete(id);
                  }}
                  showRaw={showRaw}
                />
              </>
            )}
          </section>

          {meeting.summaries.length > 0 && (
            <section aria-label="AI 요약" className="drawer-section">
              <h4>AI 요약</h4>
              {meeting.summaries.map((summary) => (
                <article className="summary-card" data-kind={summary.kind} data-summary-id={summary.summary_id} key={summary.summary_id}>
                  <div className="summary-head">
                    <span className="ax-card-kicker">{summary.kind === "provisional" ? "실시간 요약 초안" : "확정 요약"}</span>
                    <span className="t-meta">v{summary.version}</span>
                  </div>
                  <p className="prewrap">{summary.body}</p>
                  {summary.evidence.length > 0 && (
                    <ul className="summary-evidence">
                      {summary.evidence.map((evidence, index) => (
                        <li key={`${summary.summary_id}-${evidence.statement_index}`}>
                          <button className="btn link" onClick={() => showEvidence(summary, index)} type="button">
                            근거 {offset(evidence.raw_start_ms)}
                          </button>
                          <span>{evidence.text}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {summary.state !== "adopted" && meeting.note?.lifecycle !== "finalized" && (
                    <button className="btn h30 primary" disabled={busy} onClick={() => void adopt(summary)} type="button">
                      회의록으로 채택
                    </button>
                  )}
                  {summary.state === "adopted" && <small className="t-meta">채택됨 · 회의록 새 버전으로 들어갔습니다</small>}
                </article>
              ))}
            </section>
          )}
          {personaId === meeting.owner_id && <p className="t-meta">이 회의의 소유자입니다.</p>}
        </>
      )}
    </Drawer>
  );
}
