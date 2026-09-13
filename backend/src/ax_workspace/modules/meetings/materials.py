"""회의 자료 — 회의를 보며 열어 놓을 파일 (SCAX-SPEC-004 §5.7 · §10).

업무 자료와 **같은 결**이다: 같은 Attachment 원장, 같은 저장소 port, 같은 추출 경로. 다른 것은 게이트뿐 —
붙이고 떼는 것은 참석자이고 떼는 것은 **올린 사람**이며, 회의가 도는 동안에는 자료 자리가 서지 않는다.

데모가 받는 형식은 **PDF · Markdown 둘**이고 한 파일 20MB 까지다 (§2.2 · §10). 되는 것만 붙고,
안 되는 것은 **왜 안 됐는지와 함께** 돌려준다 — 여러 개를 한 번에 올릴 때 하나가 막혔다고 나머지를 버리지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.meetings.domain import (
    MeetingAccessDenied,
    MeetingError,
    MeetingNotFound,
    MeetingStateConflict,
)
from ax_workspace.modules.meetings.material_policy import (
    ACCEPTED_CONTENT_TYPES,
    MeetingMaterialContext,
    accepts,
    decide_material_access,
    inline_media_type as inline_media_type,
    is_detachable,
)


#: 한 파일 한도. 업무 자료(25MB)보다 좁다 — 회의는 보며 여는 자리이지 보관하는 자리가 아니다.
MAX_MEETING_MATERIAL_BYTES = 20 * 1024 * 1024

REASON_TOO_LARGE = "too_large"
REASON_UNSUPPORTED = "unsupported_type"

CONTEXT_TYPE = "meeting"
ROLE = "reference"


@dataclass(frozen=True, slots=True)
class MaterialUpload:
    name: str
    content_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class RejectedUpload:
    name: str
    reason: str


class MeetingMaterialGate(Protocol):
    """회의 쪽 판정 — 이 사람이 이 회의를 여는가, 참석자인가, 지금 자료를 다룰 수 있는가."""

    def readable(self, principal: Any, meeting_id: UUID) -> Any: ...
    def is_attendee(self, principal: Any, meeting: Any) -> bool: ...


class MeetingMaterialApplication:
    """회의에 붙은 파일들. 저장은 업무 자료와 같은 자리이고 판정만 회의의 것이다."""

    def __init__(
        self,
        gate: MeetingMaterialGate,
        attachments: Any,
        storage: Any,
        extractions: Any = None,
        extraction_queue: Any = None,
    ) -> None:
        self._gate = gate
        self._attachments = attachments
        self._storage = storage
        self._extractions = extractions
        self._extraction_queue = extraction_queue

    # --- 읽기 -------------------------------------------------------------------

    def list(self, principal: Any, meeting_id: UUID) -> list[dict[str, Any]]:
        """회의를 열 수 있는 사람이면 자료 목록을 본다 — 공유받은 사람도 읽는다 (SPEC-004 §3.2-4)."""
        meeting = self._gate.readable(principal, meeting_id)
        actor_is_attendee = self._gate.is_attendee(principal, meeting)
        return [
            self._view(
                binding,
                attachment,
                principal=principal,
                meeting=meeting,
                actor_is_attendee=actor_is_attendee,
            )
            for binding, attachment in self._active(meeting_id)
        ]

    def open(self, principal: Any, meeting_id: UUID, material_id: UUID) -> tuple[dict[str, Any], bytes]:
        meeting = self._gate.readable(principal, meeting_id)
        found = next(
            ((binding, attachment) for binding, attachment in self._active(meeting_id) if attachment.id == material_id),
            None,
        )
        if found is None:
            raise MeetingNotFound("meeting material was not found")
        binding, attachment = found
        if attachment.source_kind != "file":
            raise MeetingError("only file attachments have downloadable content")
        if getattr(attachment, "lifecycle", "available") == "purged":
            raise MeetingNotFound("이 자료는 완전히 삭제되어 더 이상 내려받을 수 없습니다")
        try:
            data = self._storage.get(attachment.source_ref)
        except FileNotFoundError as error:
            raise MeetingNotFound("자료 원본을 찾을 수 없습니다") from error
        return self._view(
            binding,
            attachment,
            principal=principal,
            meeting=meeting,
            actor_is_attendee=self._gate.is_attendee(principal, meeting),
        ), data

    # --- 쓰기 -------------------------------------------------------------------

    def attach(
        self, principal: Any, meeting_id: UUID, uploads: list[MaterialUpload]
    ) -> tuple[list[dict[str, Any]], list[RejectedUpload]]:
        """되는 것만 붙이고 안 되는 것은 사유와 함께 돌려준다 (SPEC-004 §10).

        붙이는 것은 **참석자 전원**이다 (§3.3). 회의가 도는 동안에는 자료 자리가 서지 않는다 (§5.1).
        """
        meeting = self._writable(principal, meeting_id)
        if not uploads:
            raise MeetingError("attach at least one file")

        attached: list[dict[str, Any]] = []
        failed: list[RejectedUpload] = []
        for upload in uploads:
            name = (upload.name or "").strip().replace("/", "_").replace("\\", "_")[:300] or "material"
            if len(upload.data) > MAX_MEETING_MATERIAL_BYTES:
                failed.append(RejectedUpload(name, REASON_TOO_LARGE))
                continue
            if not accepts(name, upload.content_type):
                failed.append(RejectedUpload(name, REASON_UNSUPPORTED))
                continue
            if not upload.data:
                failed.append(RejectedUpload(name, REASON_UNSUPPORTED))
                continue
            attached.append(self._store(principal, meeting, name, upload))
        if not attached and failed:
            # 하나도 붙지 않았다면 올린 사람에게는 실패한 요청이다 — 사유는 그대로 싣는다.
            raise MeetingMaterialsRejected(failed)
        return attached, failed

    def detach(self, principal: Any, meeting_id: UUID, material_id: UUID) -> None:
        """떼는 것은 **올린 사람**이고, 뗄 수 있는 자리는 **「예정」 하나**다 (사용자 결정 D34).

        회의가 시작된 뒤로는 그 자료가 회의에서 실제로 쓰인 것이라 떼면 기록이 어긋난다 — 붙이는 것과
        달리 떼는 것은 되돌릴 수 없는 쪽이므로 자리를 더 좁게 둔다.
        binding 만 끊는다: 원본과 그 자료의 다른 자리는 그대로다.
        """
        meeting = self._detachable(principal, meeting_id)
        found = next(
            ((binding, attachment) for binding, attachment in self._active(meeting.id) if attachment.id == material_id),
            None,
        )
        if found is None:
            raise MeetingNotFound("meeting material was not found")
        binding, attachment = found
        access = decide_material_access(
            MeetingMaterialContext(
                status=meeting.status,
                actor_is_attendee=True,
                actor_id=str(principal.id),
                uploaded_by=str(attachment.uploaded_by),
            )
        )
        if not access.can_detach:
            raise MeetingAccessDenied("only the person who attached this material may remove it")
        self._attachments.unbind(binding)

    # --- 안 -------------------------------------------------------------------

    def _writable(self, principal: Any, meeting_id: UUID) -> Any:
        """붙이는 자리 — 회의가 도는 동안만 아니면 된다 (SPEC-004 §5.1 「진행 중」 행)."""
        meeting = self._gate.readable(principal, meeting_id)
        attendee = self._gate.is_attendee(principal, meeting)
        access = decide_material_access(
            MeetingMaterialContext(
                status=meeting.status,
                actor_is_attendee=attendee,
                actor_id=str(principal.id),
                uploaded_by=None,
            )
        )
        if not attendee:
            raise MeetingAccessDenied("only an attendee may change this meeting's materials")
        if not access.can_attach:
            raise MeetingStateConflict("materials are not attached or removed while the meeting is running")
        return meeting

    def _detachable(self, principal: Any, meeting_id: UUID) -> Any:
        """떼는 자리 — **「예정」 하나다** (D34). 붙이는 자리보다 좁은 것은 의도다.

        화면이 읽는 `can_detach` 와 **같은 판정**을 쓴다: 단추가 서지 않는 자리에서 요청만 통과하면
        규칙이 두 곳에 살게 된다.
        """
        meeting = self._gate.readable(principal, meeting_id)
        if not self._gate.is_attendee(principal, meeting):
            raise MeetingAccessDenied("only an attendee may change this meeting's materials")
        if not is_detachable(meeting.status):
            raise MeetingStateConflict("materials are removed only while the meeting is still scheduled")
        return meeting

    def _active(self, meeting_id: UUID) -> list[tuple[Any, Any]]:
        return [
            (binding, attachment)
            for binding, attachment in self._attachments.bindings_for(CONTEXT_TYPE, str(meeting_id))
            if binding.unbound_at is None
        ]

    def _store(self, principal: Any, meeting: Any, name: str, upload: MaterialUpload) -> dict[str, Any]:
        from ax_workspace.modules.work.material_extraction import MaterialExtractionJob
        from ax_workspace.modules.work.materials import store_file

        attachment = store_file(
            self._attachments,
            self._storage,
            key_prefix=f"meetings/{meeting.id}",
            name=name,
            content_type=_media_type(name, upload.content_type),
            data=upload.data,
            provenance=f"upload by {principal.id} to meeting {meeting.id}",
            uploaded_by=str(principal.id),
        )
        binding = self._attachments.bind(
            attachment_id=attachment.id,
            context_type=CONTEXT_TYPE,
            context_id=str(meeting.id),
            role=ROLE,
            bound_by=str(principal.id),
        )
        if self._extractions is not None:
            # 자료가 있는 그 트랜잭션에 추출 잡도 있다 — 붙었는데 읽히지 않는 자료를 남기지 않는다.
            extraction = self._extractions.request(attachment)
            if extraction.status == "queued" and self._extraction_queue is not None:
                self._extraction_queue.enqueue(MaterialExtractionJob(extraction.id, attachment.id))
        return self._view(
            binding,
            attachment,
            principal=principal,
            meeting=meeting,
            actor_is_attendee=True,
        )

    def _view(
        self,
        binding: Any,
        attachment: Any,
        *,
        principal: Any,
        meeting: Any,
        actor_is_attendee: bool,
    ) -> dict[str, Any]:
        """화면이 읽는 자료 한 줄. **저장 위치는 나가지 않는다.**

        `can_detach` 는 **서버가 말한다** — 화면이 「올린 사람이 나인가」를 스스로 맞춰 보면 상태 조건이
        빠지고, 규칙이 두 곳에 살게 된다. 떼는 자리는 **「예정」 하나**이므로(D34) 판정도 그 하나다.
        """
        del binding
        access = decide_material_access(
            MeetingMaterialContext(
                status=meeting.status,
                actor_is_attendee=actor_is_attendee,
                actor_id=str(principal.id),
                uploaded_by=str(attachment.uploaded_by),
            )
        )
        return {
            "material_id": str(attachment.id),
            "name": attachment.name,
            "content_type": attachment.content_type,
            "size": int(attachment.size_bytes or 0),
            "uploaded_by": attachment.uploaded_by,
            "uploaded_at": _iso(attachment.created_at),
            "can_detach": access.can_detach,
        }


class MeetingMaterialsRejected(MeetingError):
    """한 파일도 붙지 않았다 — 왜 안 됐는지를 그대로 싣는다."""

    def __init__(self, failed: list[RejectedUpload]) -> None:
        super().__init__("no meeting material was accepted")
        self.failed = failed


def _media_type(name: str, content_type: str) -> str:
    """선언을 믿되 비어 있으면 이름이 말하는 것으로 채운다 — 내려받을 때 그 형식으로 열린다."""
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared in ACCEPTED_CONTENT_TYPES:
        return declared
    return "application/pdf" if name.lower().endswith(".pdf") else "text/markdown"


def _iso(value: Any) -> str | None:
    return None if value is None else value.isoformat()
