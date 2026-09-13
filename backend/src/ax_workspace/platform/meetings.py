"""SQLAlchemy adapter for Meeting-owned identity, authorization relations, and note history."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ax_workspace.platform.persistence import (
    ActivityEventRecord,
    EmploymentPeriodRecord,
    MeetingAgendaRecord,
    MeetingAttendeeRecord,
    MeetingLineRecord,
    MeetingAiSessionRecord,
    MeetingBatchRunRecord,
    MeetingRecordingFileRecord,
    MeetingTodoRecord,
    MeetingTranscriptRecord,
    MeetingRecord,
    MemberCredentialRecord,
    MemberRecord,
    MembershipRecord,
    ResourceRelationshipRecord,
)

_MEETING_SHARE_KINDS = ("share", "legacy_public_share")


def normalize_todo_title(value: str) -> str:
    """승격된 후보를 다시 세우지 않기 위한 비교 축. 합성이 같은 일을 다시 뽑아도 그 자리는 하나다."""
    return "".join(character for character in (value or "").lower() if character.isalnum())


class SqlAlchemyMeetingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        organization_id: str,
        owner_id: str,
        title: str | None,
        purpose: str | None,
        starts_at: datetime,
        ends_at: datetime,
        location: str | None,
        status: str,
        attendee_ids: list[str],
        external_attendees: list[str],
        carried_from_meeting_id: UUID | None,
        started_at: datetime | None = None,
    ) -> MeetingRecord:
        now = datetime.now(UTC)
        meeting = MeetingRecord(
            organization_id=organization_id,
            owner_id=owner_id,
            title=title,
            purpose=purpose,
            location=location,
            starts_at=starts_at,
            ends_at=ends_at,
            status=status,
            started_at=started_at,
            external_attendees=list(external_attendees),
            carried_from_meeting_id=carried_from_meeting_id,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._session.add(meeting)
        self._session.flush()
        for member_id in attendee_ids:
            self._session.add(
                MeetingAttendeeRecord(
                    meeting_id=meeting.id,
                    member_id=member_id,
                    invited_by=owner_id,
                    attendance_state="invited",
                    added_at=now,
                )
            )
        return meeting

    def replace_attendees(self, meeting: MeetingRecord, attendee_ids: list[str], actor_id: str) -> None:
        """참석은 관계다 — 뺀 사람은 지우지 않고 `removed_at`으로 닫아 누가 언제 빠졌는지가 남는다."""
        now = datetime.now(UTC)
        wanted = list(dict.fromkeys(attendee_ids))
        rows = list(
            self._session.scalars(
                select(MeetingAttendeeRecord).where(MeetingAttendeeRecord.meeting_id == meeting.id)
            )
        )
        live = {row.member_id: row for row in rows if row.removed_at is None}
        for member_id, row in live.items():
            if member_id not in wanted:
                row.removed_at = now
        for member_id in wanted:
            if member_id in live:
                continue
            closed = next((row for row in rows if row.member_id == member_id and row.removed_at is not None), None)
            if closed is not None:
                closed.removed_at = None
                closed.added_at = now
                continue
            self._session.add(
                MeetingAttendeeRecord(
                    meeting_id=meeting.id,
                    member_id=member_id,
                    invited_by=actor_id,
                    attendance_state="invited",
                    added_at=now,
                )
            )
        self._session.flush()

    def primary_organization(self, member_id: str) -> str | None:
        membership = self._session.scalar(
            select(MembershipRecord)
            .where(MembershipRecord.member_id == member_id)
            .order_by(MembershipRecord.is_primary.desc(), MembershipRecord.valid_from)
        )
        return membership.organization_id if membership is not None else None

    def meetings_in_organizations(self, organization_ids: frozenset[str]) -> list[MeetingRecord]:
        if not organization_ids:
            return []
        return list(
            self._session.scalars(
                select(MeetingRecord)
                .where(MeetingRecord.organization_id.in_(organization_ids))
                .order_by(MeetingRecord.starts_at, MeetingRecord.id)
            )
        )

    def meetings_visible_to(self, organization_ids: frozenset[str], member_id: str) -> list[MeetingRecord]:
        """조직 범위 안의 회의에 더해, 그 사람이 참석했거나 공유받은 회의를 함께 낸다.

        열람은 조직이 아니라 관계로 갈린다 — 부서를 가로지른 회의와 공유받은 회의가 조직 범위 질의에서 새면 안 된다.
        """
        now = datetime.now(UTC)
        related = select(MeetingAttendeeRecord.meeting_id).where(
            MeetingAttendeeRecord.member_id == member_id, MeetingAttendeeRecord.removed_at.is_(None)
        )
        shared = select(ResourceRelationshipRecord.resource_id).where(
            ResourceRelationshipRecord.resource_type == "meeting",
            ResourceRelationshipRecord.relationship_kind.in_(_MEETING_SHARE_KINDS),
            ResourceRelationshipRecord.member_id == member_id,
            ResourceRelationshipRecord.valid_from <= now,
            or_(ResourceRelationshipRecord.valid_until.is_(None), ResourceRelationshipRecord.valid_until > now),
        )
        shared_ids = [UUID(value) for value in self._session.scalars(shared) if value]
        conditions = [MeetingRecord.id.in_(related), MeetingRecord.owner_id == member_id]
        if organization_ids:
            conditions.append(MeetingRecord.organization_id.in_(organization_ids))
        if shared_ids:
            conditions.append(MeetingRecord.id.in_(shared_ids))
        return list(
            self._session.scalars(
                select(MeetingRecord).where(or_(*conditions)).order_by(MeetingRecord.starts_at, MeetingRecord.id)
            )
        )

    def meeting(self, meeting_id: UUID, *, lock: bool = False) -> MeetingRecord | None:
        statement = select(MeetingRecord).where(MeetingRecord.id == meeting_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self._session.scalar(statement)

    def attendee_ids(self, meeting: MeetingRecord) -> set[str]:
        return set(
            self._session.scalars(
                select(MeetingAttendeeRecord.member_id).where(
                    MeetingAttendeeRecord.meeting_id == meeting.id,
                    MeetingAttendeeRecord.removed_at.is_(None),
                )
            )
        )

    def is_shared_with(self, meeting: MeetingRecord, member_id: str) -> bool:
        now = datetime.now(UTC)
        return (
            self._session.scalar(
                select(ResourceRelationshipRecord.id).where(
                    ResourceRelationshipRecord.resource_type == "meeting",
                    ResourceRelationshipRecord.resource_id == str(meeting.id),
                    ResourceRelationshipRecord.relationship_kind.in_(_MEETING_SHARE_KINDS),
                    ResourceRelationshipRecord.member_id == member_id,
                    ResourceRelationshipRecord.valid_from <= now,
                    or_(
                        ResourceRelationshipRecord.valid_until.is_(None),
                        ResourceRelationshipRecord.valid_until > now,
                    ),
                )
            )
            is not None
        )

    def shared_member_ids(self, meeting: MeetingRecord) -> list[str]:
        """지금 이 회의를 공유로 열어 둔 사람들 — 참석과 별개의 근거다."""
        now = datetime.now(UTC)
        return list(
            self._session.scalars(
                select(ResourceRelationshipRecord.member_id).where(
                    ResourceRelationshipRecord.resource_type == "meeting",
                    ResourceRelationshipRecord.resource_id == str(meeting.id),
                    ResourceRelationshipRecord.relationship_kind.in_(_MEETING_SHARE_KINDS),
                    ResourceRelationshipRecord.valid_from <= now,
                    or_(
                        ResourceRelationshipRecord.valid_until.is_(None),
                        ResourceRelationshipRecord.valid_until > now,
                    ),
                )
            )
        )

    def member_display_name(self, member_id: str) -> str | None:
        member = self._session.get(MemberRecord, member_id)
        return member.display_name if member is not None else None

    def member_email(self, member_id: str) -> str | None:
        """로컬 계정 이메일. 데모 도메인이라 사옥 예약 시스템의 계정과 어긋날 수 있다 — 그때는 이름으로 잇는다."""
        credential = self._session.get(MemberCredentialRecord, member_id)
        return credential.email if credential is not None else None

    def is_active_member_in_organization(self, member_id: str, organization_id: str) -> bool:
        now = datetime.now(UTC)
        return (
            self._session.scalar(
                select(MemberRecord.id)
                .join(EmploymentPeriodRecord, EmploymentPeriodRecord.member_id == MemberRecord.id)
                .join(MembershipRecord, MembershipRecord.member_id == MemberRecord.id)
                .where(
                    MemberRecord.id == member_id,
                    MemberRecord.employment_state == "active",
                    EmploymentPeriodRecord.state == "active",
                    EmploymentPeriodRecord.ended_at.is_(None),
                    MembershipRecord.organization_id == organization_id,
                    MembershipRecord.valid_from <= now,
                    or_(MembershipRecord.valid_until.is_(None), MembershipRecord.valid_until > now),
                )
            )
            is not None
        )

    def is_active_member(self, member_id: str) -> bool:
        """회의는 부서를 가로지른다 — 참석 자격은 회사에 살아 있는 사람인가 하나다 (SPEC-004 §3.1-5)."""
        now = datetime.now(UTC)
        return (
            self._session.scalar(
                select(MemberRecord.id)
                .join(EmploymentPeriodRecord, EmploymentPeriodRecord.member_id == MemberRecord.id)
                .join(MembershipRecord, MembershipRecord.member_id == MemberRecord.id)
                .where(
                    MemberRecord.id == member_id,
                    MemberRecord.employment_state == "active",
                    EmploymentPeriodRecord.state == "active",
                    EmploymentPeriodRecord.ended_at.is_(None),
                    MembershipRecord.valid_from <= now,
                    or_(MembershipRecord.valid_until.is_(None), MembershipRecord.valid_until > now),
                )
            )
            is not None
        )

    def add_share(self, meeting: MeetingRecord, member_id: str, actor_id: str) -> None:
        self._add_share(meeting, member_id, relationship_kind="share")

    def add_legacy_public_share(self, meeting: MeetingRecord, member_id: str, actor_id: str) -> None:
        self._add_share(meeting, member_id, relationship_kind="legacy_public_share")

    def _add_share(self, meeting: MeetingRecord, member_id: str, *, relationship_kind: str) -> None:
        now = datetime.now(UTC)
        existing = self._session.scalar(
            select(ResourceRelationshipRecord)
            .where(
                ResourceRelationshipRecord.resource_type == "meeting",
                ResourceRelationshipRecord.resource_id == str(meeting.id),
                ResourceRelationshipRecord.relationship_kind.in_(_MEETING_SHARE_KINDS),
                ResourceRelationshipRecord.member_id == member_id,
            )
            .order_by(ResourceRelationshipRecord.valid_from.desc())
        )
        if existing is not None and existing.valid_until is None:
            return
        self._session.add(
            ResourceRelationshipRecord(
                member_id=member_id,
                resource_type="meeting",
                resource_id=str(meeting.id),
                relationship_kind=relationship_kind,
                valid_from=now,
            )
        )

    def revoke_share(self, meeting: MeetingRecord, member_id: str) -> bool:
        relationship = self._session.scalar(
            select(ResourceRelationshipRecord)
            .where(
                ResourceRelationshipRecord.resource_type == "meeting",
                ResourceRelationshipRecord.resource_id == str(meeting.id),
                ResourceRelationshipRecord.relationship_kind.in_(_MEETING_SHARE_KINDS),
                ResourceRelationshipRecord.member_id == member_id,
                ResourceRelationshipRecord.valid_until.is_(None),
            )
            .with_for_update()
        )
        if relationship is None:
            return False
        relationship.valid_until = datetime.now(UTC)
        return True

    def revoke_legacy_public_shares(self, meeting: MeetingRecord) -> int:
        now = datetime.now(UTC)
        relationships = self._session.scalars(
            select(ResourceRelationshipRecord)
            .where(
                ResourceRelationshipRecord.resource_type == "meeting",
                ResourceRelationshipRecord.resource_id == str(meeting.id),
                ResourceRelationshipRecord.relationship_kind == "legacy_public_share",
                ResourceRelationshipRecord.valid_until.is_(None),
            )
            .with_for_update()
        ).all()
        for relationship in relationships:
            relationship.valid_until = now
        return len(relationships)

    def touch(self, meeting: MeetingRecord) -> None:
        meeting.updated_at = datetime.now(UTC)

    def append_audit(
        self,
        meeting: MeetingRecord,
        actor_id: str,
        event_kind: str,
        summary: str,
        *,
        before_ref: str | None = None,
    ) -> None:
        self._session.add(
            ActivityEventRecord(
                target_type="meeting",
                target_id=str(meeting.id),
                event_kind=event_kind,
                actor_kind="member",
                actor_id=actor_id,
                before_ref=before_ref,
                after_ref=f"meeting:{meeting.id}@{meeting.version}",
                reason=None,
                safe_summary=summary[:300],
                occurred_at=datetime.now(UTC),
            )
        )

    # ------------------------------------------------------------------ 안건 · 줄 · 다음 할 일

    def agendas(self, meeting: MeetingRecord) -> list[MeetingAgendaRecord]:
        return list(
            self._session.scalars(
                select(MeetingAgendaRecord)
                .where(MeetingAgendaRecord.meeting_id == meeting.id)
                .order_by(MeetingAgendaRecord.order_index, MeetingAgendaRecord.created_at)
            )
        )

    def agenda(self, meeting: MeetingRecord, agenda_id: UUID, *, lock: bool = False) -> MeetingAgendaRecord | None:
        statement = select(MeetingAgendaRecord).where(
            MeetingAgendaRecord.id == agenda_id, MeetingAgendaRecord.meeting_id == meeting.id
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self._session.scalar(statement)

    def agenda_count(self, meeting: MeetingRecord) -> int:
        return int(
            self._session.scalar(
                select(func.count(MeetingAgendaRecord.id)).where(MeetingAgendaRecord.meeting_id == meeting.id)
            )
            or 0
        )

    def next_agenda_order(self, meeting: MeetingRecord) -> int:
        highest = self._session.scalar(
            select(func.max(MeetingAgendaRecord.order_index)).where(MeetingAgendaRecord.meeting_id == meeting.id)
        )
        return int(highest) + 1 if highest is not None else 1

    def create_agenda(
        self, meeting: MeetingRecord, *, title: str, source: str, order_index: int,
        title_placeholder: bool = False,
    ) -> MeetingAgendaRecord:
        now = datetime.now(UTC)
        agenda = MeetingAgendaRecord(
            meeting_id=meeting.id,
            order_index=order_index,
            title=title,
            source=source,
            concluded=False,
            title_placeholder=title_placeholder,
            created_at=now,
            updated_at=now,
        )
        self._session.add(agenda)
        self._session.flush()
        return agenda

    def touch_agenda(self, agenda: MeetingAgendaRecord) -> None:
        agenda.updated_at = datetime.now(UTC)

    def delete_agenda(self, agenda: MeetingAgendaRecord) -> None:
        """안건을 지우면 그 안건의 줄과 다음 할 일이 함께 사라진다 (SPEC §4.1-8)."""
        self._session.execute(delete(MeetingTodoRecord).where(MeetingTodoRecord.agenda_id == agenda.id))
        self._session.execute(delete(MeetingLineRecord).where(MeetingLineRecord.agenda_id == agenda.id))
        self._session.delete(agenda)
        self._session.flush()

    def lines(self, meeting: MeetingRecord) -> list[MeetingLineRecord]:
        return list(
            self._session.scalars(
                select(MeetingLineRecord)
                .where(MeetingLineRecord.meeting_id == meeting.id)
                .order_by(MeetingLineRecord.agenda_id, MeetingLineRecord.order_index, MeetingLineRecord.created_at)
            )
        )

    def line_count(self, meeting: MeetingRecord) -> int:
        return int(
            self._session.scalar(
                select(func.count(MeetingLineRecord.id)).where(MeetingLineRecord.meeting_id == meeting.id)
            )
            or 0
        )

    def append_line(
        self,
        agenda: MeetingAgendaRecord,
        *,
        track: str,
        text: str,
        author_id: str | None,
        evidence: list[dict[str, Any]] | None = None,
        at_ms: int | None = None,
    ) -> MeetingLineRecord:
        """줄 하나를 안건 끝에 매단다. `at_ms` 는 메모 줄에만 실린다 — 서버가 매긴 값이다."""
        now = datetime.now(UTC)
        highest = self._session.scalar(
            select(func.max(MeetingLineRecord.order_index)).where(
                MeetingLineRecord.agenda_id == agenda.id, MeetingLineRecord.track == track
            )
        )
        line = MeetingLineRecord(
            meeting_id=agenda.meeting_id,
            agenda_id=agenda.id,
            track=track,
            order_index=int(highest) + 1 if highest is not None else 1,
            text=text,
            author_id=author_id,
            at_ms=at_ms,
            evidence=list(evidence or []),
            created_at=now,
            updated_at=now,
        )
        self._session.add(line)
        self._session.flush()
        return line

    def replace_lines(
        self, agenda: MeetingAgendaRecord, *, track: str, texts: list[str], author_id: str | None
    ) -> list[MeetingLineRecord]:
        """그 안건의 그 트랙 줄 목록을 통째로 다시 쓴다 — 판을 쌓지 않고 덮어쓴다 (SPEC §8-9 · §11.1)."""
        now = datetime.now(UTC)
        self._session.execute(
            delete(MeetingLineRecord).where(
                MeetingLineRecord.agenda_id == agenda.id, MeetingLineRecord.track == track
            )
        )
        written = []
        for order, text in enumerate(texts, start=1):
            line = MeetingLineRecord(
                meeting_id=agenda.meeting_id,
                agenda_id=agenda.id,
                track=track,
                order_index=order,
                text=text,
                author_id=author_id,
                evidence=[],
                created_at=now,
                updated_at=now,
            )
            self._session.add(line)
            written.append(line)
        self._session.flush()
        return written

    def memo_lines(self, meeting: MeetingRecord) -> list[MeetingLineRecord]:
        """메모 트랙 줄 전량, 시각 순. 「스크립트」 탭이 발화와 섞어 낸다 (SPEC-004 §5.4-8)."""
        return list(
            self._session.scalars(
                select(MeetingLineRecord)
                .where(MeetingLineRecord.meeting_id == meeting.id, MeetingLineRecord.track == "memo")
                .order_by(MeetingLineRecord.at_ms, MeetingLineRecord.created_at)
            )
        )

    def clear_track(self, meeting: MeetingRecord, track: str) -> None:
        """한 트랙의 줄만 비운다 — **안건은 하나도 건드리지 않는다.**

        종료 합성이 쓰는 자리다: 회의록을 새로 쓰되 **지금 서 있는 안건은 그대로 둔다**. 이어 쓰는
        안건의 `agenda_id` 가 그 자리에서 풀려야 하기 때문이다 — 배치처럼 AI 안건을 먼저 지워 버리면
        AI 가 이어 쓰겠다고 적어 준 id 가 허공을 가리킨다 (사용자 결정 2026-09-11).
        """
        self._session.execute(
            delete(MeetingLineRecord).where(
                MeetingLineRecord.meeting_id == meeting.id, MeetingLineRecord.track == track
            )
        )
        self._session.flush()

    def replace_track(self, meeting: MeetingRecord, track: str) -> None:
        """한 트랙을 비운다 — 전량 교체의 첫 걸음. **검증이 끝난 뒤에만 부른다** (SPEC-004 §7.1 적재).

        AI 가 세운 안건(source ai)도 함께 지운다. 사람이 만든 안건은 건드리지 않는다 (§7.3).

        **지우는 차례가 있다**: 회의 중 배치가 세운 AI 안건에는 그 배치가 낸 `ai` 줄이 매달려 있다.
        안건을 먼저 지우면 그 줄들이 안건을 붙들어 외래키가 끊긴다 — 매달린 줄을 **먼저** 떼고 안건을 지운다.

        사람이 남긴 것은 지우지 않는다: 메모 줄이 매달린 AI 안건은 **지우지 않고 남긴다** — 메모는 사람의
        기록이고, 자리가 사라지면 그 기록도 사라진다. 남은 안건은 아래 적재가 `agenda_id` 로 다시 집는다.
        """
        ai_agenda_ids = set(
            self._session.scalars(
                select(MeetingAgendaRecord.id).where(
                    MeetingAgendaRecord.meeting_id == meeting.id, MeetingAgendaRecord.source == "ai"
                )
            )
        )
        self._session.execute(
            delete(MeetingLineRecord).where(
                MeetingLineRecord.meeting_id == meeting.id, MeetingLineRecord.track == track
            )
        )
        self._session.flush()
        if ai_agenda_ids:
            # 트랙을 비우고도 사람의 줄(memo)이 남아 있는 AI 안건은 그 사람의 기록을 이고 있다 — 남긴다.
            kept = set(
                self._session.scalars(
                    select(MeetingLineRecord.agenda_id).where(
                        MeetingLineRecord.agenda_id.in_(ai_agenda_ids), MeetingLineRecord.track == "memo"
                    )
                )
            )
            ai_agenda_ids -= kept
        if ai_agenda_ids:
            # 지울 안건에 매달린 줄을 먼저 뗀다 — 남아 있는 것은 배치가 낸 `ai` 줄이다.
            self._session.execute(
                delete(MeetingLineRecord).where(MeetingLineRecord.agenda_id.in_(ai_agenda_ids))
            )
            self._session.execute(
                delete(MeetingTodoRecord).where(MeetingTodoRecord.agenda_id.in_(ai_agenda_ids))
            )
            self._session.flush()
            self._session.execute(
                delete(MeetingAgendaRecord).where(MeetingAgendaRecord.id.in_(ai_agenda_ids))
            )
        self._session.flush()

    def todos(self, meeting: MeetingRecord) -> list[MeetingTodoRecord]:
        return list(
            self._session.scalars(
                select(MeetingTodoRecord)
                .where(MeetingTodoRecord.meeting_id == meeting.id)
                .order_by(MeetingTodoRecord.agenda_id, MeetingTodoRecord.order_index)
            )
        )

    def replace_provisional_todos(self, meeting: MeetingRecord, drafts: list[dict[str, Any]]) -> None:
        """회의 **중** 후보 전량 교체 — 배치가 도는 자리다 (D46).

        AI 트랙 줄과 같은 결이다: 그 회차 출력이 곧 전체이므로 앞 회차 것을 지우고 이번 것으로 채운다.
        **확정된 후보(`provisional=false`)는 건드리지 않는다** — 승격됐거나 최종이 낸 것이다.
        """
        self.clear_provisional_todos(meeting)
        now = datetime.now(UTC)
        for draft in drafts:
            self._session.add(
                MeetingTodoRecord(
                    meeting_id=meeting.id,
                    agenda_id=draft["agenda_id"],
                    order_index=draft["order_index"],
                    title=draft["title"],
                    description=draft["description"],
                    due_candidate=draft["due_candidate"],
                    checklist_candidate=list(draft["checklist_candidate"]),
                    reference=dict(draft["reference"]),
                    provisional=True,
                    created_at=now,
                )
            )
        self._session.flush()

    def clear_provisional_todos(self, meeting: MeetingRecord) -> None:
        """회의 중 후보를 모두 거둔다 — 다음 배치가 시작할 때와 최종 합성이 시작할 때 부른다."""
        self._session.execute(
            delete(MeetingTodoRecord).where(
                MeetingTodoRecord.meeting_id == meeting.id, MeetingTodoRecord.provisional.is_(True)
            )
        )
        self._session.flush()

    def replace_todos(self, meeting: MeetingRecord, drafts: list[dict[str, Any]]) -> None:
        """다음 할 일 전량 교체 — **승격된 후보는 유지한다** (SPEC-004 §9-6 「줄은 목록에 남는다」).

        승격은 남에게 간 요청이므로 합성이 다시 돌아도 그 자리를 지운다는 뜻이 될 수 없다.
        회의 중 후보(`provisional`)는 이 자리에 오기 전에 이미 지워져 있다 — 최종이 낸 것만 남는다 (D46).
        """
        promoted = list(
            self._session.scalars(
                select(MeetingTodoRecord).where(
                    MeetingTodoRecord.meeting_id == meeting.id,
                    MeetingTodoRecord.linked_work_request_id.is_not(None),
                )
            )
        )
        kept = {normalize_todo_title(row.title) for row in promoted}
        self._session.execute(
            delete(MeetingTodoRecord).where(
                MeetingTodoRecord.meeting_id == meeting.id,
                MeetingTodoRecord.linked_work_request_id.is_(None),
            )
        )
        now = datetime.now(UTC)
        for draft in drafts:
            if normalize_todo_title(draft["title"]) in kept:
                continue
            self._session.add(
                MeetingTodoRecord(
                    meeting_id=meeting.id,
                    agenda_id=draft["agenda_id"],
                    order_index=draft["order_index"],
                    title=draft["title"],
                    description=draft["description"],
                    due_candidate=draft["due_candidate"],
                    checklist_candidate=list(draft["checklist_candidate"]),
                    reference=dict(draft["reference"]),
                    created_at=now,
                )
            )
        self._session.flush()

    def todo(self, meeting: MeetingRecord, todo_id: UUID, *, lock: bool = False) -> MeetingTodoRecord | None:
        statement = select(MeetingTodoRecord).where(
            MeetingTodoRecord.id == todo_id, MeetingTodoRecord.meeting_id == meeting.id
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self._session.scalar(statement)

    def link_todo(self, todo: MeetingTodoRecord, *, work_request_id: UUID) -> None:
        todo.linked_work_request_id = work_request_id
        self._session.flush()

    def delete_todo(self, todo: MeetingTodoRecord) -> None:
        self._session.delete(todo)
        self._session.flush()

    def next_meeting_after(self, meeting: MeetingRecord) -> MeetingRecord | None:
        """이어진 다음 회의 — 이 회의를 이월한 회의이거나, 같은 조직의 다음 예약이다 (SPEC-004 §8.2 기한 ②)."""
        carried = self._session.scalar(
            select(MeetingRecord)
            .where(MeetingRecord.carried_from_meeting_id == meeting.id)
            .order_by(MeetingRecord.starts_at)
        )
        if carried is not None:
            return carried
        return self._session.scalar(
            select(MeetingRecord)
            .where(
                MeetingRecord.organization_id == meeting.organization_id,
                MeetingRecord.owner_id == meeting.owner_id,
                MeetingRecord.starts_at > meeting.starts_at,
                MeetingRecord.status == "scheduled",
            )
            .order_by(MeetingRecord.starts_at)
        )

    def delete_note_content(self, meeting: MeetingRecord) -> None:
        """[회의록만 삭제] — 회의록의 줄과 다음 할 일을 지우고 회의 예약은 남긴다 (SPEC §3.1-9).

        안건은 회의 예약이 담은 것이므로 남는다. 자료는 SCAX-WP-005가 이 자리에 얹는다.
        """
        self._session.execute(delete(MeetingTodoRecord).where(MeetingTodoRecord.meeting_id == meeting.id))
        self._session.execute(delete(MeetingLineRecord).where(MeetingLineRecord.meeting_id == meeting.id))
        meeting.last_saved_at = None
        self._session.flush()

    # ------------------------------------------------------------------ 확정 발화 · 오디오 원본

    def append_transcript_block(
        self, meeting_id: UUID, *, speaker_label: str, at_ms: int, end_ms: int, text: str
    ) -> MeetingTranscriptRecord:
        """확정 블록 한 행. `seq` 는 회의 안에서 이어지는 번호다 — 원문을 읽는 순서가 곧 그 순서다."""
        highest = self._session.scalar(
            select(func.max(MeetingTranscriptRecord.seq)).where(MeetingTranscriptRecord.meeting_id == meeting_id)
        )
        block = MeetingTranscriptRecord(
            meeting_id=meeting_id,
            seq=int(highest) + 1 if highest is not None else 1,
            speaker_label=speaker_label,
            at_ms=at_ms,
            end_ms=end_ms,
            text=text,
            created_at=datetime.now(UTC),
        )
        self._session.add(block)
        self._session.flush()
        return block

    def replace_transcript(self, meeting_id: UUID, blocks: list[Any]) -> int:
        """원문을 **통째로 갈아 끼운다** — 종료 뒤 재전사가 부르는 자리다 (D44).

        판을 쌓지 않으므로 지우고 다시 넣는다. `seq` 는 1부터 다시 매긴다: 근거가 딛는 것은 `at_ms` 이지
        행 번호가 아니고, 새 원문은 블록 경계가 달라 옛 번호를 이어 쓸 수 없다.
        """
        self._session.execute(
            delete(MeetingTranscriptRecord).where(MeetingTranscriptRecord.meeting_id == meeting_id)
        )
        self._session.flush()
        now = datetime.now(UTC)
        for seq, block in enumerate(blocks, start=1):
            self._session.add(
                MeetingTranscriptRecord(
                    meeting_id=meeting_id,
                    seq=seq,
                    speaker_label=block.speaker_label,
                    at_ms=int(block.at_ms),
                    end_ms=int(block.end_ms),
                    text=block.content,
                    created_at=now,
                )
            )
        self._session.flush()
        return len(blocks)

    def transcript_blocks(self, meeting: MeetingRecord) -> list[MeetingTranscriptRecord]:
        return list(
            self._session.scalars(
                select(MeetingTranscriptRecord)
                .where(MeetingTranscriptRecord.meeting_id == meeting.id)
                .order_by(MeetingTranscriptRecord.seq)
            )
        )

    def transcript_blocks_after(self, meeting_id: UUID, after_seq: int) -> list[MeetingTranscriptRecord]:
        return list(
            self._session.scalars(
                select(MeetingTranscriptRecord)
                .where(MeetingTranscriptRecord.meeting_id == meeting_id, MeetingTranscriptRecord.seq > after_seq)
                .order_by(MeetingTranscriptRecord.seq)
            )
        )

    def transcript_speaker_count(self, meeting_id: UUID) -> int:
        return int(
            self._session.scalar(
                select(func.count(func.distinct(MeetingTranscriptRecord.speaker_label))).where(
                    MeetingTranscriptRecord.meeting_id == meeting_id
                )
            )
            or 0
        )

    def record_recording_file(self, meeting_id: UUID, *, storage_key: str, content_type: str) -> None:
        """회의당 한 행. 이미 있으면 갱신 시각만 옮긴다 — 원본은 계속 자라는 파일 하나다."""
        now = datetime.now(UTC)
        row = self._session.scalar(
            select(MeetingRecordingFileRecord).where(MeetingRecordingFileRecord.meeting_id == meeting_id)
        )
        if row is None:
            self._session.add(
                MeetingRecordingFileRecord(
                    meeting_id=meeting_id,
                    storage_key=storage_key,
                    content_type=content_type,
                    size_bytes=0,
                    started_at=now,
                    updated_at=now,
                )
            )
        else:
            row.storage_key = storage_key
            row.updated_at = now
        self._session.flush()

    # ------------------------------------------------------------------ AI 세션 · 배치 회차

    def record_ai_session(self, meeting_id: UUID, *, provider_session_ref: str, persona_id: str) -> None:
        """회의당 한 행. 다시 열면 참조만 갈아 끼운다 — 회의당 세션 하나가 계약이다."""
        row = self._session.scalar(
            select(MeetingAiSessionRecord).where(MeetingAiSessionRecord.meeting_id == meeting_id)
        )
        if row is None:
            self._session.add(
                MeetingAiSessionRecord(
                    meeting_id=meeting_id,
                    provider_session_ref=provider_session_ref,
                    persona_id=persona_id,
                    opened_at=datetime.now(UTC),
                )
            )
        else:
            row.provider_session_ref = provider_session_ref
            row.persona_id = persona_id
        self._session.flush()

    def ai_session(self, meeting_id: UUID) -> MeetingAiSessionRecord | None:
        return self._session.scalar(
            select(MeetingAiSessionRecord).where(MeetingAiSessionRecord.meeting_id == meeting_id)
        )

    def succeeded_batch_cursor(self, meeting_id: UUID) -> int:
        """마지막으로 **성공한** 배치가 읽은 마지막 블록 번호. 실패·폐기 구간은 여기 들지 않는다."""
        return int(
            self._session.scalar(
                select(func.max(MeetingBatchRunRecord.to_seq)).where(
                    MeetingBatchRunRecord.meeting_id == meeting_id,
                    MeetingBatchRunRecord.status == "succeeded",
                )
            )
            or 0
        )

    def next_batch_seq(self, meeting_id: UUID) -> int:
        highest = self._session.scalar(
            select(func.max(MeetingBatchRunRecord.seq)).where(MeetingBatchRunRecord.meeting_id == meeting_id)
        )
        return int(highest) + 1 if highest is not None else 1

    def latest_succeeded_batch_seq(self, meeting_id: UUID) -> int:
        return int(
            self._session.scalar(
                select(func.max(MeetingBatchRunRecord.seq)).where(
                    MeetingBatchRunRecord.meeting_id == meeting_id,
                    MeetingBatchRunRecord.status == "succeeded",
                )
            )
            or 0
        )

    def record_batch_run(
        self,
        meeting_id: UUID,
        *,
        seq: int,
        status: str,
        trigger_cause: str,
        from_seq: int | None,
        to_seq: int | None,
        reason: str | None = None,
    ) -> None:
        self._session.add(
            MeetingBatchRunRecord(
                meeting_id=meeting_id,
                seq=seq,
                status=status,
                trigger_cause=trigger_cause,
                from_seq=from_seq,
                to_seq=to_seq,
                reason=(reason or None) and reason[:2000],
                created_at=datetime.now(UTC),
            )
        )
        self._session.flush()

    def pending_transcript_chars(self, meeting_id: UUID, after_seq: int) -> int:
        """아직 배치가 읽지 않은 확정 발화의 글자 수 — 분량 트리거의 축이다."""
        total = 0
        for block in self.transcript_blocks_after(meeting_id, after_seq):
            total += len(block.text)
        return total

    def recording_file(self, meeting_id: UUID) -> MeetingRecordingFileRecord | None:
        """감사·용량 확인용. **응답으로 나가지 않는다** (SCAX-SPEC-004 §5.5-4)."""
        return self._session.scalar(
            select(MeetingRecordingFileRecord).where(MeetingRecordingFileRecord.meeting_id == meeting_id)
        )
