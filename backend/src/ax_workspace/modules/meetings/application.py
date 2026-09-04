"""Public Meeting commands and authorized projections.

This module knows no transport or ORM. The repository owns persistence and append-only
audit facts; HTTP, MCP, Calendar, Materials, and AX call these commands rather than tables.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.meetings.domain import (
    MeetingAccessDenied,
    MeetingError,
    MeetingNotFound,
    MeetingVersionConflict,
)
from ax_workspace.modules.meetings.recordings import RecordingStorage
from ax_workspace.modules.meetings.refinement import RefinedTranscriptSegment
from ax_workspace.modules.meetings.summary import SummaryStatement
from ax_workspace.modules.meetings.transcription import FinalTranscriptSegment, RealtimeTranscriptionKeyIssuer
from ax_workspace.modules.organization_access.domain import MEETING_RECORD, Principal


MEETING_READ = "meeting.read"
MEETING_READ_PRIVATE = "meeting.read.private"
MEETING_MANAGE = "meeting.manage"
MEETING_SHARE = "meeting.share"


class MeetingRepository(Protocol):
    def create(
        self,
        *,
        organization_id: str,
        owner_id: str,
        title: str,
        starts_at: datetime,
        ends_at: datetime,
        visibility: str,
        attendee_ids: list[str],
    ) -> Any: ...
    def meetings_in_organizations(self, organization_ids: frozenset[str]) -> list[Any]: ...
    def meeting(self, meeting_id: UUID, *, lock: bool = False) -> Any | None: ...
    def attendee_ids(self, meeting: Any) -> set[str]: ...
    def is_shared_with(self, meeting: Any, member_id: str) -> bool: ...
    def member_display_name(self, member_id: str) -> str | None: ...
    def is_active_member_in_organization(self, member_id: str, organization_id: str) -> bool: ...
    def add_share(self, meeting: Any, member_id: str, actor_id: str) -> None: ...
    def revoke_share(self, meeting: Any, member_id: str) -> bool: ...
    def touch(self, meeting: Any) -> None: ...
    def append_audit(self, meeting: Any, actor_id: str, event_kind: str, summary: str, *, before_ref: str | None = None) -> None: ...
    def note(self, meeting: Any, *, lock: bool = False) -> Any | None: ...
    def create_note(
        self,
        meeting: Any,
        body: str,
        author_id: str,
        source_evidence: list[dict[str, Any]] | None = None,
    ) -> Any: ...
    def append_note_version(self, note: Any, body: str, author_id: str, source_evidence: list[dict[str, Any]] | None = None) -> Any: ...
    def note_versions(self, note: Any) -> list[Any]: ...
    def finalize_note(self, note: Any, actor_id: str) -> None: ...
    def create_recording(self, meeting: Any, actor_id: str, purpose: str) -> Any: ...
    def recording(self, meeting: Any, recording_id: UUID, *, lock: bool = False) -> Any | None: ...
    def recording_by_id(self, recording_id: UUID, *, lock: bool = False) -> Any | None: ...
    def recordings(self, meeting: Any) -> list[Any]: ...
    def complete_recording(self, recording: Any, stored: Any) -> None: ...
    def raw_transcript_for_provider(self, recording: Any, provider_reference: str) -> Any | None: ...
    def latest_raw_transcript_for_recording(self, recording: Any) -> Any | None: ...
    def create_raw_transcript(
        self,
        recording: Any,
        *,
        provider: str,
        provider_reference: str,
        segments: list[FinalTranscriptSegment],
    ) -> Any: ...
    def raw_transcript_segments(self, transcript: Any) -> list[Any]: ...
    def raw_transcript(self, transcript_id: UUID, *, lock: bool = False) -> Any | None: ...
    def latest_refinement(self, transcript: Any) -> Any | None: ...
    def refinement(self, refinement_id: UUID, *, lock: bool = False) -> Any | None: ...
    def refinement_segments(self, refinement: Any) -> list[Any]: ...
    def create_refinement(
        self,
        transcript: Any,
        *,
        provider_call_ref: str | None,
        content_hash: str,
        segments: list[RefinedTranscriptSegment],
    ) -> Any: ...
    def summary_for_refinement(self, refinement: Any, kind: str) -> Any | None: ...
    def create_summary(
        self,
        refinement: Any,
        *,
        kind: str,
        body: str,
        provider_call_ref: str | None,
        content_hash: str,
        statements: list[SummaryStatement],
    ) -> Any: ...
    def summary_evidence(self, summary: Any) -> list[Any]: ...
    def summary(self, meeting: Any, summary_id: UUID, *, lock: bool = False) -> Any | None: ...
    def adopt_summary(self, summary: Any, note_version: Any, actor_id: str) -> None: ...
    def speaker_assignments(self, transcript: Any) -> list[Any]: ...
    def assign_speaker_identity(
        self,
        meeting: Any,
        transcript: Any,
        *,
        speaker_label: str,
        member_id: str,
        scope: str,
        raw_start_segment: Any,
        raw_end_segment: Any,
        confirmed_by: str,
    ) -> Any: ...
    def mark_recording_transcribing(self, recording: Any, lease_token: UUID) -> None: ...
    def complete_recording_finalization(self, recording: Any) -> None: ...
    def mark_recording_failed(self, recording: Any, code: str) -> None: ...
    def mark_recording_retryable(self, recording: Any, code: str) -> None: ...


class MeetingApplication:
    def __init__(
        self,
        repository: MeetingRepository,
        recording_storage: RecordingStorage,
        realtime_key_issuer: RealtimeTranscriptionKeyIssuer,
    ) -> None:
        self._repository = repository
        self._recording_storage = recording_storage
        self._realtime_key_issuer = realtime_key_issuer

    def list(self, principal: Principal) -> list[dict[str, Any]]:
        """Calendar-safe projection: concealed private meetings contribute only a time busy block."""
        rows: list[dict[str, Any]] = []
        for meeting in self._repository.meetings_in_organizations(principal.organization_scope):
            if self._can_read_detail(principal, meeting):
                rows.append(self._view(meeting, include_note=False))
            else:
                rows.append({"kind": "busy", "starts_at": _iso(meeting.starts_at), "ends_at": _iso(meeting.ends_at)})
        return rows

    def get(self, principal: Principal, meeting_id: UUID) -> dict[str, Any]:
        meeting = self._repository.meeting(meeting_id)
        if meeting is None or not self._can_read_detail(principal, meeting):
            # Detail lookup deliberately fails closed, unlike calendar's busy-only projection.
            raise MeetingNotFound("meeting was not found")
        return self._view(meeting, include_note=True)

    def create(
        self,
        principal: Principal,
        *,
        organization_id: str,
        title: str,
        starts_at: datetime,
        ends_at: datetime,
        visibility: str,
        attendee_ids: list[str],
    ) -> dict[str, Any]:
        self._require(principal, MEETING_MANAGE)
        self._validate_schedule(title, starts_at, ends_at, visibility)
        if organization_id not in principal.organization_scope:
            raise MeetingAccessDenied("meeting organization is outside the principal scope")
        attendees = _distinct(attendee_ids)
        for member_id in attendees:
            if not self._repository.is_active_member_in_organization(member_id, organization_id):
                raise MeetingError("attendee is not an active member in the meeting organization")
        meeting = self._repository.create(
            organization_id=organization_id,
            owner_id=str(principal.id),
            title=title.strip(),
            starts_at=starts_at,
            ends_at=ends_at,
            visibility=visibility,
            attendee_ids=attendees,
        )
        self._repository.append_audit(meeting, str(principal.id), "meeting.created", f"회의 생성: {meeting.title}")
        return self._view(meeting, include_note=True)

    def update(self, principal: Principal, meeting_id: UUID, expected_version: int, changes: dict[str, Any]) -> dict[str, Any]:
        self._require(principal, MEETING_MANAGE)
        meeting = self._owned_mutable_meeting(principal, meeting_id, expected_version)
        unknown = set(changes) - {"title", "starts_at", "ends_at", "visibility"}
        if unknown:
            raise MeetingError(f"unsupported meeting fields: {sorted(unknown)}")
        title = str(changes.get("title", meeting.title)).strip()
        starts_at = changes.get("starts_at", meeting.starts_at)
        ends_at = changes.get("ends_at", meeting.ends_at)
        visibility = str(changes.get("visibility", meeting.visibility))
        self._validate_schedule(title, starts_at, ends_at, visibility)
        meeting.title = title
        meeting.starts_at = starts_at
        meeting.ends_at = ends_at
        meeting.visibility = visibility
        meeting.version += 1
        self._repository.touch(meeting)
        self._repository.append_audit(meeting, str(principal.id), "meeting.updated", f"회의 수정: {meeting.title}", before_ref=f"meeting:{meeting.id}@{expected_version}")
        return self._view(meeting, include_note=True)

    def share(self, principal: Principal, meeting_id: UUID, member_id: str, expected_version: int) -> dict[str, Any]:
        self._require(principal, MEETING_SHARE)
        meeting = self._owned_mutable_meeting(principal, meeting_id, expected_version)
        if not self._repository.is_active_member_in_organization(member_id, meeting.organization_id):
            raise MeetingError("share target is not an active member in the meeting organization")
        self._repository.add_share(meeting, member_id, str(principal.id))
        meeting.version += 1
        self._repository.touch(meeting)
        self._repository.append_audit(meeting, str(principal.id), "meeting.shared", "회의 열람 공유", before_ref=f"meeting:{meeting.id}@{expected_version}")
        return self._view(meeting, include_note=True)

    def revoke_share(self, principal: Principal, meeting_id: UUID, member_id: str, expected_version: int) -> dict[str, Any]:
        self._require(principal, MEETING_SHARE)
        meeting = self._owned_mutable_meeting(principal, meeting_id, expected_version)
        if not self._repository.revoke_share(meeting, member_id):
            raise MeetingNotFound("meeting share was not found")
        meeting.version += 1
        self._repository.touch(meeting)
        self._repository.append_audit(meeting, str(principal.id), "meeting.share_revoked", "회의 열람 공유 회수", before_ref=f"meeting:{meeting.id}@{expected_version}")
        return self._view(meeting, include_note=True)

    def create_note(self, principal: Principal, meeting_id: UUID, body: str) -> dict[str, Any]:
        meeting = self._note_target(principal, meeting_id)
        if not body.strip():
            raise MeetingError("meeting note body is required")
        if self._repository.note(meeting) is not None:
            raise MeetingError("meeting note already exists")
        note = self._repository.create_note(meeting, body.strip(), str(principal.id))
        self._repository.append_audit(meeting, str(principal.id), "meeting.note_created", "회의록 작성")
        return self._note_view(note)

    def save_note(self, principal: Principal, meeting_id: UUID, expected_version: int, body: str) -> dict[str, Any]:
        meeting = self._note_target(principal, meeting_id)
        if not body.strip():
            raise MeetingError("meeting note body is required")
        note = self._repository.note(meeting, lock=True)
        if note is None:
            raise MeetingNotFound("meeting note was not found")
        if note.current_version != expected_version:
            raise MeetingVersionConflict("meeting note version is stale")
        if note.lifecycle == "finalized":
            raise MeetingError("finalized meeting notes cannot be edited")
        version = self._repository.append_note_version(note, body.strip(), str(principal.id))
        self._repository.append_audit(meeting, str(principal.id), "meeting.note_saved", "회의록 새 버전 저장", before_ref=f"meeting_note:{note.id}@{expected_version}")
        return self._note_view(note, current=version)

    def finalize_note(self, principal: Principal, meeting_id: UUID, expected_version: int) -> dict[str, Any]:
        meeting = self._note_target(principal, meeting_id)
        note = self._repository.note(meeting, lock=True)
        if note is None:
            raise MeetingNotFound("meeting note was not found")
        if note.current_version != expected_version:
            raise MeetingVersionConflict("meeting note version is stale")
        if note.lifecycle == "finalized":
            return self._note_view(note)
        self._repository.finalize_note(note, str(principal.id))
        self._repository.append_audit(meeting, str(principal.id), "meeting.note_finalized", "회의록 확정")
        return self._note_view(note)

    def start_recording(self, principal: Principal, meeting_id: UUID, purpose: str) -> dict[str, Any]:
        meeting = self._recording_target(principal, meeting_id)
        if not purpose.strip():
            raise MeetingError("recording purpose is required")
        recording = self._repository.create_recording(meeting, str(principal.id), purpose.strip())
        self._repository.append_audit(meeting, str(principal.id), "meeting.recording_started", "회의 녹음 시작")
        return self._recording_view(recording)

    def stop_recording(
        self,
        principal: Principal,
        meeting_id: UUID,
        recording_id: UUID,
        expected_version: int,
        *,
        original_name: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        meeting = self._recording_target(principal, meeting_id)
        recording = self._repository.recording(meeting, recording_id, lock=True)
        if recording is None:
            raise MeetingNotFound("meeting recording was not found")
        if recording.actor_id != str(principal.id):
            raise MeetingAccessDenied("only the recording initiator may stop this recording")
        if recording.version != expected_version:
            raise MeetingVersionConflict("meeting recording version is stale")
        if recording.state != "recording":
            raise MeetingError("only a recording in progress can be stopped")
        stored = self._recording_storage.put(
            recording_id=str(recording.id),
            original_name=original_name,
            content_type=content_type,
            data=data,
        )
        self._repository.complete_recording(recording, stored)
        self._repository.append_audit(meeting, str(principal.id), "meeting.recording_uploaded", "회의 녹음 업로드 완료", before_ref=f"meeting_recording:{recording.id}@{expected_version}")
        return self._recording_view(recording)

    def issue_realtime_credential(
        self,
        principal: Principal,
        meeting_id: UUID,
        recording_id: UUID,
        max_session_duration_seconds: int,
    ) -> dict[str, Any]:
        meeting = self._recording_target(principal, meeting_id)
        recording = self._repository.recording(meeting, recording_id)
        if recording is None or recording.state != "recording":
            raise MeetingNotFound("active meeting recording was not found")
        if recording.actor_id != str(principal.id):
            raise MeetingAccessDenied("only the recording initiator may open its realtime stream")
        credential = self._realtime_key_issuer.issue(
            client_reference_id=recording.provider_client_reference_id,
            max_session_duration_seconds=max_session_duration_seconds,
        )
        return {
            "temporary_key": credential.temporary_key,
            "expires_at": _iso(credential.expires_at),
            "client_reference_id": credential.client_reference_id,
            "websocket_url": credential.websocket_url,
            "model": credential.model,
            "enable_speaker_diarization": credential.enable_speaker_diarization,
        }

    def finalization_input(
        self,
        recording_id: UUID,
        *,
        lease_token: UUID,
        stale_after_seconds: int,
    ) -> dict[str, Any]:
        """Worker claim: move an uploaded recording into transcribing in a short transaction."""
        recording = self._repository.recording_by_id(recording_id, lock=True)
        if recording is None:
            raise MeetingNotFound("meeting recording was not found")
        if recording.state == "transcribed":
            return {"completed": True}
        reclaiming_stale_attempt = False
        if recording.state == "transcribing":
            started = recording.finalization_started_at
            if started is not None and started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if started is not None and datetime.now(UTC) - started < timedelta(seconds=stale_after_seconds):
                return {"contended": True}
            reclaiming_stale_attempt = True
        if recording.state not in {"uploaded", "transcribing"} or not recording.storage_key:
            raise MeetingError("meeting recording cannot be finalized")
        self._repository.mark_recording_transcribing(recording, lease_token)
        raw = self._repository.latest_raw_transcript_for_recording(recording)
        if raw is not None:
            refinement = self._repository.latest_refinement(raw)
            if refinement is not None and refinement.state == "completed":
                summary = self._repository.summary_for_refinement(refinement, "final")
                if summary is not None and summary.state == "completed":
                    # Crash after derived saves but before the recording state transition.
                    self._repository.complete_recording_finalization(recording)
                    return {"completed": True}
                return {
                    "stage": "summary",
                    "refinement_revision_id": str(refinement.id),
                    "reclaimed_stale_attempt": reclaiming_stale_attempt,
                }
            return {
                "stage": "refinement",
                "transcript_revision_id": str(raw.id),
                "reclaimed_stale_attempt": reclaiming_stale_attempt,
            }
        return {
            "stage": "transcribe",
            "recording_id": str(recording.id),
            "storage_key": recording.storage_key,
            "original_name": recording.original_name or "recording",
            "content_type": recording.content_type or "application/octet-stream",
            "client_reference_id": recording.provider_client_reference_id,
            "reclaimed_stale_attempt": reclaiming_stale_attempt,
        }

    def complete_finalization(self, recording_id: UUID, *, lease_token: UUID) -> None:
        """Mark the Recording complete only after immutable raw, refinement, and final summary exist."""
        recording = self._repository.recording_by_id(recording_id, lock=True)
        if recording is None:
            raise MeetingNotFound("meeting recording was not found")
        self._require_finalization_lease(recording, lease_token)
        raw = self._repository.latest_raw_transcript_for_recording(recording)
        refinement = raw and self._repository.latest_refinement(raw)
        summary = refinement and self._repository.summary_for_refinement(refinement, "final")
        if raw is None or refinement is None or refinement.state != "completed" or summary is None or summary.state != "completed":
            raise MeetingError("meeting transcript pipeline is incomplete")
        self._repository.complete_recording_finalization(recording)

    def fail_finalization(self, recording_id: UUID, *, lease_token: UUID, code: str) -> None:
        recording = self._repository.recording_by_id(recording_id, lock=True)
        if recording is None:
            raise MeetingNotFound("meeting recording was not found")
        self._require_finalization_lease(recording, lease_token)
        self._repository.mark_recording_failed(recording, code)
        meeting = self._repository.meeting(recording.meeting_id)
        if meeting is not None:
            self._repository.append_audit(meeting, recording.actor_id, "meeting.transcription_failed", "회의 전사 실패")

    def retry_finalization(self, recording_id: UUID, *, lease_token: UUID, code: str) -> None:
        recording = self._repository.recording_by_id(recording_id, lock=True)
        if recording is None:
            raise MeetingNotFound("meeting recording was not found")
        self._require_finalization_lease(recording, lease_token)
        self._repository.mark_recording_retryable(recording, code)

    def record_finalization_cleanup_warning(
        self,
        recording_id: UUID,
        *,
        lease_token: UUID,
        warnings: tuple[str, ...],
    ) -> None:
        if not warnings:
            return
        recording = self._repository.recording_by_id(recording_id, lock=True)
        if recording is None:
            raise MeetingNotFound("meeting recording was not found")
        self._require_finalization_lease(recording, lease_token)
        meeting = self._repository.meeting(recording.meeting_id)
        if meeting is not None:
            self._repository.append_audit(
                meeting,
                recording.actor_id,
                "meeting.transcription_provider_cleanup_warning",
                "외부 전사 정리 확인 필요",
            )

    @staticmethod
    def _require_finalization_lease(recording: Any, lease_token: UUID) -> None:
        if recording.finalization_lease_token != lease_token:
            raise MeetingVersionConflict("meeting recording finalization lease is stale")

    def record_final_transcript(
        self,
        *,
        recording_id: UUID,
        provider: str,
        provider_reference: str,
        segments: list[FinalTranscriptSegment],
        finalization_lease_token: UUID | None = None,
    ) -> dict[str, Any]:
        """Worker-only canonical write for immutable async STT output.

        The provider reference is an idempotency key. A redelivery returns the
        original revision rather than changing raw text, even if the provider
        later supplies different bytes.
        """
        if not provider.strip() or not provider_reference.strip():
            raise MeetingError("final transcript provider provenance is required")
        if not segments:
            raise MeetingError("final transcript requires at least one segment")
        try:
            for segment in segments:
                segment.validate()
        except ValueError as error:
            raise MeetingError(str(error)) from error
        recording = self._repository.recording_by_id(recording_id, lock=True)
        if recording is None:
            raise MeetingNotFound("meeting recording was not found")
        if finalization_lease_token is not None and recording.finalization_lease_token != finalization_lease_token:
            raise MeetingVersionConflict("meeting recording finalization lease is stale")
        existing = self._repository.raw_transcript_for_provider(recording, provider_reference)
        if existing is not None:
            return self._raw_transcript_view(existing)
        if recording.state not in {"uploaded", "transcribing"}:
            raise MeetingError("only an uploaded recording can receive final transcript output")
        transcript = self._repository.create_raw_transcript(
            recording,
            provider=provider.strip(),
            provider_reference=provider_reference.strip(),
            segments=segments,
        )
        self._repository.append_audit(
            self._repository.meeting(recording.meeting_id),
            recording.actor_id,
            "meeting.transcript_finalized",
            "회의 원본 STT 확정",
            before_ref=f"meeting_recording:{recording.id}@{recording.version}",
        )
        return self._raw_transcript_view(transcript)

    def refinement_input(self, transcript_id: UUID) -> dict[str, Any]:
        """Read-only worker input. Existing completed refinement makes a retry a no-op."""
        transcript = self._repository.raw_transcript(transcript_id)
        if transcript is None:
            raise MeetingNotFound("meeting raw transcript was not found")
        existing = self._repository.latest_refinement(transcript)
        if existing is not None and existing.state == "completed":
            return {"completed": self._refinement_view(existing)}
        raw_segments = self._repository.raw_transcript_segments(transcript)
        return {
            "transcript_revision_id": str(transcript.id),
            "raw_segments": [
                {
                    "source_segment_key": segment.source_segment_key,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "text": segment.text,
                    "speaker_label": segment.speaker_label,
                }
                for segment in raw_segments
            ],
        }

    def save_refinement(
        self,
        transcript_id: UUID,
        *,
        provider_call_ref: str | None,
        content_hash: str,
        segments: list[RefinedTranscriptSegment],
        finalization_lease_token: UUID | None = None,
    ) -> dict[str, Any]:
        transcript = self._repository.raw_transcript(transcript_id, lock=True)
        if transcript is None:
            raise MeetingNotFound("meeting raw transcript was not found")
        if finalization_lease_token is not None:
            recording = self._repository.recording_by_id(transcript.recording_id, lock=True)
            if recording is None:
                raise MeetingNotFound("meeting recording was not found")
            self._require_finalization_lease(recording, finalization_lease_token)
        existing = self._repository.latest_refinement(transcript)
        if existing is not None and existing.state == "completed":
            return self._refinement_view(existing)
        self._validate_refinement_coverage(self._repository.raw_transcript_segments(transcript), segments)
        refinement = self._repository.create_refinement(
            transcript,
            provider_call_ref=provider_call_ref,
            content_hash=content_hash,
            segments=segments,
        )
        return self._refinement_view(refinement)

    def summary_input(self, refinement_id: UUID, *, kind: str) -> dict[str, Any]:
        if kind not in {"provisional", "final"}:
            raise MeetingError("summary kind is invalid")
        refinement = self._repository.refinement(refinement_id)
        if refinement is None or refinement.state != "completed":
            raise MeetingNotFound("completed transcript refinement was not found")
        existing = self._repository.summary_for_refinement(refinement, kind)
        if existing is not None and existing.state == "completed":
            return {"completed": self._summary_view(existing)}
        return {
            "refinement_revision_id": str(refinement.id),
            "kind": kind,
            "segments": [
                {
                    "sequence": segment.sequence,
                    "text": segment.text,
                    "speaker_label": segment.speaker_label,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                }
                for segment in self._repository.refinement_segments(refinement)
            ],
        }

    def save_summary(
        self,
        refinement_id: UUID,
        *,
        kind: str,
        body: str,
        provider_call_ref: str | None,
        content_hash: str,
        statements: list[SummaryStatement],
        finalization_lease_token: UUID | None = None,
    ) -> dict[str, Any]:
        refinement = self._repository.refinement(refinement_id, lock=True)
        if refinement is None or refinement.state != "completed":
            raise MeetingNotFound("completed transcript refinement was not found")
        if finalization_lease_token is not None:
            raw = self._repository.raw_transcript(refinement.raw_transcript_revision_id)
            recording = raw and self._repository.recording_by_id(raw.recording_id, lock=True)
            if recording is None:
                raise MeetingNotFound("meeting recording was not found")
            self._require_finalization_lease(recording, finalization_lease_token)
        existing = self._repository.summary_for_refinement(refinement, kind)
        if existing is not None and existing.state == "completed":
            return self._summary_view(existing)
        self._validate_summary_evidence(self._repository.refinement_segments(refinement), statements)
        summary = self._repository.create_summary(
            refinement,
            kind=kind,
            body=body,
            provider_call_ref=provider_call_ref,
            content_hash=content_hash,
            statements=statements,
        )
        return self._summary_view(summary)

    def adopt_summary(
        self,
        principal: Principal,
        meeting_id: UUID,
        summary_id: UUID,
        expected_version: int,
    ) -> dict[str, Any]:
        """Human adoption appends a NoteVersion; it never mutates a suggestion or transcript."""
        meeting = self._note_target(principal, meeting_id)
        summary = self._repository.summary(meeting, summary_id, lock=True)
        if summary is None or summary.state not in {"completed", "adopted"}:
            raise MeetingNotFound("meeting summary suggestion was not found")
        if summary.version != expected_version:
            raise MeetingVersionConflict("meeting summary version is stale")
        if summary.state == "adopted":
            return {"summary": self._summary_view(summary), "note": self._note_view(self._repository.note(meeting))}
        note = self._repository.note(meeting, lock=True)
        if note is not None and note.lifecycle == "finalized":
            raise MeetingError("a finalized meeting note cannot adopt a summary")
        if note is None:
            note = self._repository.create_note(
                meeting,
                summary.body or "",
                str(principal.id),
                source_evidence=self._summary_source_evidence(summary),
            )
            note_version = self._repository.note_versions(note)[-1]
        else:
            note_version = self._repository.append_note_version(
                note,
                summary.body or "",
                str(principal.id),
                source_evidence=self._summary_source_evidence(summary),
            )
        self._repository.adopt_summary(summary, note_version, str(principal.id))
        self._repository.append_audit(
            meeting,
            str(principal.id),
            "meeting.summary_adopted",
            "AI 회의 요약 채택",
            before_ref=f"meeting_summary:{summary.id}@{expected_version}",
        )
        return {"summary": self._summary_view(summary), "note": self._note_view(note, current=note_version)}

    def assign_speaker_identity(
        self,
        principal: Principal,
        meeting_id: UUID,
        transcript_revision_id: UUID,
        *,
        speaker_label: str,
        member_id: str,
        scope: str,
        raw_start_source_key: str,
        raw_end_source_key: str,
    ) -> dict[str, Any]:
        meeting = self._note_target(principal, meeting_id)
        transcript = self._repository.raw_transcript(transcript_revision_id)
        if transcript is None:
            raise MeetingNotFound("meeting raw transcript was not found")
        recording = self._repository.recording_by_id(transcript.recording_id)
        if recording is None or recording.meeting_id != meeting.id:
            raise MeetingNotFound("meeting raw transcript was not found")
        if scope not in {"segment_range", "speaker_track"} or not speaker_label.strip():
            raise MeetingError("speaker mapping scope and label are required")
        if not self._repository.is_active_member_in_organization(member_id, meeting.organization_id):
            raise MeetingError("speaker mapping member is not active in the meeting organization")
        raw_segments = self._repository.raw_transcript_segments(transcript)
        by_key = {segment.source_segment_key: segment for segment in raw_segments}
        start = by_key.get(raw_start_source_key)
        end = by_key.get(raw_end_source_key)
        if start is None or end is None or start.sequence > end.sequence:
            raise MeetingError("speaker mapping source range is invalid")
        assignment = self._repository.assign_speaker_identity(
            meeting,
            transcript,
            speaker_label=speaker_label.strip(),
            member_id=member_id,
            scope=scope,
            raw_start_segment=start,
            raw_end_segment=end,
            confirmed_by=str(principal.id),
        )
        self._repository.append_audit(meeting, str(principal.id), "meeting.speaker_confirmed", "회의 화자 확인")
        return self._speaker_assignment_view(assignment)

    def _owned_mutable_meeting(self, principal: Principal, meeting_id: UUID, expected_version: int) -> Any:
        meeting = self._repository.meeting(meeting_id, lock=True)
        if meeting is None:
            raise MeetingNotFound("meeting was not found")
        if meeting.owner_id != str(principal.id):
            raise MeetingAccessDenied("only the meeting owner may change this meeting")
        if meeting.version != expected_version:
            raise MeetingVersionConflict("meeting version is stale")
        return meeting

    def _note_target(self, principal: Principal, meeting_id: UUID) -> Any:
        self._require(principal, MEETING_MANAGE)
        meeting = self._repository.meeting(meeting_id)
        if meeting is None or not self._can_read_detail(principal, meeting):
            raise MeetingNotFound("meeting was not found")
        if str(principal.id) != meeting.owner_id and str(principal.id) not in self._repository.attendee_ids(meeting):
            raise MeetingAccessDenied("only an attendee may edit the meeting note")
        return meeting

    def _recording_target(self, principal: Principal, meeting_id: UUID) -> Any:
        self._require(principal, MEETING_RECORD)
        meeting = self._repository.meeting(meeting_id)
        if meeting is None or not self._can_read_detail(principal, meeting):
            raise MeetingNotFound("meeting was not found")
        member_id = str(principal.id)
        if member_id != meeting.owner_id and member_id not in self._repository.attendee_ids(meeting):
            raise MeetingAccessDenied("only a meeting owner or attendee may record")
        return meeting

    def _can_read_detail(self, principal: Principal, meeting: Any) -> bool:
        if meeting.organization_id not in principal.organization_scope or MEETING_READ not in principal.capabilities:
            return False
        if meeting.visibility == "public":
            return True
        member_id = str(principal.id)
        return member_id == meeting.owner_id or member_id in self._repository.attendee_ids(meeting) or self._repository.is_shared_with(meeting, member_id) or MEETING_READ_PRIVATE in principal.capabilities

    def _view(self, meeting: Any, *, include_note: bool) -> dict[str, Any]:
        attendee_ids = sorted(self._repository.attendee_ids(meeting))
        result: dict[str, Any] = {
            "kind": "meeting", "meeting_id": str(meeting.id), "organization_id": meeting.organization_id,
            "owner_id": meeting.owner_id, "title": meeting.title, "starts_at": _iso(meeting.starts_at),
            "ends_at": _iso(meeting.ends_at), "visibility": meeting.visibility, "lifecycle": meeting.lifecycle,
            "version": meeting.version,
            "attendees": [{"member_id": member_id, "display_name": self._repository.member_display_name(member_id) or member_id} for member_id in attendee_ids],
        }
        if include_note:
            note = self._repository.note(meeting)
            result["note"] = self._note_view(note) if note is not None else None
            recordings, summaries = self._recording_records(meeting)
            result["recordings"] = recordings
            result["summaries"] = summaries
        return result

    def _recording_records(self, meeting: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Each recording with the transcript layers built on it, and every summary those layers produced.

        The layering is kept visible rather than flattened: the immutable raw revision, the versioned refinement that
        points back at it, and the human speaker confirmations that outrank both. A caller that never asks for detail
        never reaches any of this, because this runs only behind the same authorization as the meeting itself.
        """
        recordings: list[dict[str, Any]] = []
        summaries: list[dict[str, Any]] = []
        for recording in self._repository.recordings(meeting):
            view = self._recording_view(recording)
            transcript = self._repository.latest_raw_transcript_for_recording(recording)
            refinement = self._repository.latest_refinement(transcript) if transcript is not None else None
            view["raw_transcript"] = self._raw_transcript_view(transcript) if transcript is not None else None
            view["refinement"] = self._refinement_view(refinement) if refinement is not None else None
            view["speaker_assignments"] = (
                [self._speaker_assignment_view(assignment) for assignment in self._repository.speaker_assignments(transcript)]
                if transcript is not None
                else []
            )
            recordings.append(view)
            if refinement is not None:
                for kind in ("provisional", "final"):
                    summary = self._repository.summary_for_refinement(refinement, kind)
                    if summary is not None:
                        summaries.append(self._summary_view(summary))
        return recordings, summaries

    def _note_view(self, note: Any, *, current: Any | None = None) -> dict[str, Any]:
        versions = self._repository.note_versions(note)
        latest = current or (versions[-1] if versions else None)
        return {
            "note_id": str(note.id), "lifecycle": note.lifecycle, "version": note.current_version,
            "body": latest.body if latest is not None else "",
            "versions": [{"version_id": str(version.id), "version": version.version, "body": version.body, "created_by": version.created_by, "created_at": _iso(version.created_at), "source_evidence": list(version.source_evidence or [])} for version in versions],
            "finalized_at": _iso(note.finalized_at), "finalized_by": note.finalized_by,
        }

    @staticmethod
    def _recording_view(recording: Any) -> dict[str, Any]:
        return {
            "recording_id": str(recording.id),
            "meeting_id": str(recording.meeting_id),
            "purpose": recording.purpose,
            "state": recording.state,
            "version": recording.version,
            "content_type": recording.content_type,
            "original_name": recording.original_name,
            "size_bytes": recording.size_bytes,
            "sha256": recording.sha256,
            "started_at": _iso(recording.started_at),
            "ended_at": _iso(recording.ended_at),
            # A storage key or provider reference is never a browser capability.
            "storage_key": None,
        }

    def _raw_transcript_view(self, transcript: Any) -> dict[str, Any]:
        segments = self._repository.raw_transcript_segments(transcript)
        confirmed = self._confirmed_members_for_raw_segments(transcript, segments)
        return {
            "transcript_revision_id": str(transcript.id),
            "recording_id": str(transcript.recording_id),
            "revision": transcript.revision,
            "state": transcript.state,
            "source_kind": transcript.source_kind,
            "provider": transcript.provider,
            "segments": [
                {
                    "segment_id": str(segment.id),
                    "source_segment_key": segment.source_segment_key,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "text": segment.text,
                    "speaker_label": segment.speaker_label,
                    "confirmed_member_id": confirmed.get(segment.id),
                }
                for segment in segments
            ],
        }

    def _refinement_view(self, refinement: Any) -> dict[str, Any]:
        raw_transcript = self._repository.raw_transcript(refinement.raw_transcript_revision_id)
        raw_segments = self._repository.raw_transcript_segments(raw_transcript) if raw_transcript is not None else []
        confirmed = self._confirmed_members_for_raw_segments(raw_transcript, raw_segments) if raw_transcript is not None else {}
        return {
            "refinement_revision_id": str(refinement.id),
            "raw_transcript_revision_id": str(refinement.raw_transcript_revision_id),
            "revision": refinement.revision,
            "state": refinement.state,
            "provider_call_ref": refinement.provider_call_ref,
            "segments": [
                {
                    "segment_id": str(segment.id),
                    "raw_start_segment_id": str(segment.raw_start_segment_id),
                    "raw_end_segment_id": str(segment.raw_end_segment_id),
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "text": segment.text,
                    "speaker_label": segment.speaker_label,
                    "confirmed_member_id": self._confirmed_member_for_refined_span(segment, raw_segments, confirmed),
                    "correction_kind": segment.correction_kind,
                    "confidence": segment.confidence,
                }
                for segment in self._repository.refinement_segments(refinement)
            ],
        }

    def _confirmed_members_for_raw_segments(self, transcript: Any, segments: list[Any]) -> dict[UUID, str | None]:
        if transcript is None:
            return {}
        by_id = {segment.id: segment for segment in segments}
        result: dict[UUID, str | None] = {segment.id: segment.confirmed_member_id for segment in segments}
        for assignment in self._repository.speaker_assignments(transcript):
            start = by_id.get(assignment.raw_start_segment_id)
            end = by_id.get(assignment.raw_end_segment_id)
            if start is None or end is None:
                continue
            for segment in segments:
                if assignment.scope == "speaker_track":
                    applies = segment.speaker_label == assignment.speaker_label
                else:
                    applies = start.sequence <= segment.sequence <= end.sequence
                if applies:
                    result[segment.id] = assignment.member_id
        return result

    @staticmethod
    def _confirmed_member_for_refined_span(segment: Any, raw_segments: list[Any], confirmed: dict[UUID, str | None]) -> str | None:
        by_id = {row.id: row for row in raw_segments}
        start = by_id.get(segment.raw_start_segment_id)
        end = by_id.get(segment.raw_end_segment_id)
        if start is None or end is None:
            return segment.confirmed_member_id
        member_ids = {
            confirmed.get(row.id)
            for row in raw_segments
            if start.sequence <= row.sequence <= end.sequence
        }
        return next(iter(member_ids)) if len(member_ids) == 1 else None

    def _summary_view(self, summary: Any) -> dict[str, Any]:
        return {
            "summary_id": str(summary.id),
            "meeting_id": str(summary.meeting_id),
            "raw_transcript_revision_id": str(summary.raw_transcript_revision_id),
            "refinement_revision_id": str(summary.refinement_revision_id),
            "kind": summary.kind,
            "state": summary.state,
            "version": summary.version,
            "body": summary.body,
            "provider_call_ref": summary.provider_call_ref,
            "evidence": [
                {
                    "statement_index": row.statement_index,
                    "kind": row.statement_kind,
                    "text": row.statement_text,
                    "refinement_start_segment_id": str(row.refinement_start_segment_id),
                    "refinement_end_segment_id": str(row.refinement_end_segment_id),
                    "raw_start_segment_id": str(row.raw_start_segment_id),
                    "raw_end_segment_id": str(row.raw_end_segment_id),
                    "raw_start_ms": row.raw_start_ms,
                    "raw_end_ms": row.raw_end_ms,
                }
                for row in self._repository.summary_evidence(summary)
            ],
        }

    def _summary_source_evidence(self, summary: Any) -> list[dict[str, Any]]:
        return [
            {
                "summary_id": str(summary.id),
                "statement_index": row.statement_index,
                "raw_start_segment_id": str(row.raw_start_segment_id),
                "raw_end_segment_id": str(row.raw_end_segment_id),
                "raw_start_ms": row.raw_start_ms,
                "raw_end_ms": row.raw_end_ms,
            }
            for row in self._repository.summary_evidence(summary)
        ]

    @staticmethod
    def _speaker_assignment_view(assignment: Any) -> dict[str, Any]:
        return {
            "speaker_assignment_id": str(assignment.id),
            "transcript_revision_id": str(assignment.transcript_revision_id),
            "speaker_label": assignment.speaker_label,
            "member_id": assignment.member_id,
            "scope": assignment.scope,
            "raw_start_segment_id": str(assignment.raw_start_segment_id),
            "raw_end_segment_id": str(assignment.raw_end_segment_id),
            "source_audio_start_ms": assignment.source_audio_start_ms,
            "source_audio_end_ms": assignment.source_audio_end_ms,
            "source": assignment.source,
            "state": assignment.state,
        }

    @staticmethod
    def _validate_refinement_coverage(raw_segments: list[Any], refined_segments: list[RefinedTranscriptSegment]) -> None:
        if not raw_segments or not refined_segments:
            raise MeetingError("refinement must cover a non-empty raw transcript")
        positions = {row.source_segment_key: index for index, row in enumerate(raw_segments)}
        raw_by_key = {row.source_segment_key: row for row in raw_segments}
        coverage: set[int] = set()
        previous_start = -1
        for segment in refined_segments:
            if segment.raw_start_source_key not in positions or segment.raw_end_source_key not in positions:
                raise MeetingError("refinement references an unknown raw source segment")
            start = positions[segment.raw_start_source_key]
            end = positions[segment.raw_end_source_key]
            if start > end or start < previous_start:
                raise MeetingError("refinement source ranges are not ordered")
            if segment.start_ms < raw_by_key[segment.raw_start_source_key].start_ms or segment.end_ms > raw_by_key[segment.raw_end_source_key].end_ms:
                raise MeetingError("refinement timestamp is outside its raw source range")
            coverage.update(range(start, end + 1))
            previous_start = start
        if coverage != set(range(len(raw_segments))):
            raise MeetingError("refinement must preserve coverage of every raw source segment")

    @staticmethod
    def _validate_summary_evidence(refined_segments: list[Any], statements: list[SummaryStatement]) -> None:
        if not statements:
            raise MeetingError("summary requires at least one evidence-bound statement")
        sequences = {segment.sequence for segment in refined_segments}
        for statement in statements:
            if (
                statement.refinement_start_sequence not in sequences
                or statement.refinement_end_sequence not in sequences
            ):
                raise MeetingError("summary statement references an unknown refined segment")
            expected = set(range(statement.refinement_start_sequence, statement.refinement_end_sequence + 1))
            if not expected.issubset(sequences):
                raise MeetingError("summary statement must reference a contiguous refined segment range")

    @staticmethod
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise MeetingAccessDenied(f"{capability} capability is required")

    @staticmethod
    def _validate_schedule(title: str, starts_at: datetime, ends_at: datetime, visibility: str) -> None:
        if not title.strip():
            raise MeetingError("meeting title is required")
        if starts_at.tzinfo is None or ends_at.tzinfo is None:
            raise MeetingError("meeting times must include a timezone")
        if starts_at >= ends_at:
            raise MeetingError("meeting start must be before end")
        if visibility not in {"public", "private"}:
            raise MeetingError("meeting visibility must be public or private")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    # SQLite drops timezone offsets in fast contract tests; public meeting transport is always UTC.
    return (value if value.tzinfo is not None else value.replace(tzinfo=UTC)).isoformat()


def _distinct(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))
