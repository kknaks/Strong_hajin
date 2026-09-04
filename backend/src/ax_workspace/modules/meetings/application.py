"""Public Meeting commands and authorized projections.

This module knows no transport or ORM. The repository owns persistence and append-only
audit facts; HTTP, MCP, Calendar, Materials, and AX call these commands rather than tables.
"""
from __future__ import annotations

from datetime import UTC, datetime
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
from ax_workspace.modules.meetings.transcription import FinalTranscriptSegment
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
    def create_note(self, meeting: Any, body: str, author_id: str) -> Any: ...
    def append_note_version(self, note: Any, body: str, author_id: str, source_evidence: list[dict[str, Any]] | None = None) -> Any: ...
    def note_versions(self, note: Any) -> list[Any]: ...
    def finalize_note(self, note: Any, actor_id: str) -> None: ...
    def create_recording(self, meeting: Any, actor_id: str, purpose: str) -> Any: ...
    def recording(self, meeting: Any, recording_id: UUID, *, lock: bool = False) -> Any | None: ...
    def recording_by_id(self, recording_id: UUID, *, lock: bool = False) -> Any | None: ...
    def recordings(self, meeting: Any) -> list[Any]: ...
    def complete_recording(self, recording: Any, stored: Any) -> None: ...
    def raw_transcript_for_provider(self, recording: Any, provider_reference: str) -> Any | None: ...
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
    def refinement_segments(self, refinement: Any) -> list[Any]: ...
    def create_refinement(
        self,
        transcript: Any,
        *,
        provider_call_ref: str | None,
        content_hash: str,
        segments: list[RefinedTranscriptSegment],
    ) -> Any: ...


class MeetingApplication:
    def __init__(self, repository: MeetingRepository, recording_storage: RecordingStorage) -> None:
        self._repository = repository
        self._recording_storage = recording_storage

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

    def record_final_transcript(
        self,
        *,
        recording_id: UUID,
        provider: str,
        provider_reference: str,
        segments: list[FinalTranscriptSegment],
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
    ) -> dict[str, Any]:
        transcript = self._repository.raw_transcript(transcript_id, lock=True)
        if transcript is None:
            raise MeetingNotFound("meeting raw transcript was not found")
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
            result["recordings"] = [self._recording_view(recording) for recording in self._repository.recordings(meeting)]
        return result

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
                    "confirmed_member_id": segment.confirmed_member_id,
                }
                for segment in self._repository.raw_transcript_segments(transcript)
            ],
        }

    def _refinement_view(self, refinement: Any) -> dict[str, Any]:
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
                    "confirmed_member_id": segment.confirmed_member_id,
                    "correction_kind": segment.correction_kind,
                    "confidence": segment.confidence,
                }
                for segment in self._repository.refinement_segments(refinement)
            ],
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
