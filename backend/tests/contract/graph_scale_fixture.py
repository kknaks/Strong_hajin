"""Synthetic ledger population only; no company or vault data.

Stable identities and a fixed clock make the expected partitions independent of
GraphApplication. The caller must supply its own disposable, seeded test database.
"""
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from ax_workspace.platform.persistence import (
    AttachmentBindingRecord, AttachmentRecord, EmploymentPeriodRecord, MemberRecord, MembershipRecord,
    MeetingAttendeeRecord, MeetingRecord, OrganizationUnitRecord,
    ProjectRecord, TaskAssignmentRecord, TaskRecord,
    TaskReferenceRecord, WorkRequestRecord, make_session_factory,
)
from ax_workspace.platform.projects import SqlAlchemyProjectRepository

SEED = "scax-graph-scale-v1"
WHEN = datetime(2026, 1, 1, tzinfo=UTC)


def identity(kind: str, index: int):
    return uuid5(NAMESPACE_URL, f"{SEED}/{kind}/{index}")


def populate(database_url: str) -> dict:
    edges: set[tuple[str, str, str]] = set()
    def edge(kind, source, target):
        edges.add((kind, source, target))

    with make_session_factory(database_url)() as session:
        for i in range(20):
            session.add(OrganizationUnitRecord(id=f"scale-team-{i}", name=f"대량 조직 {i:02}", parent_id="scax"))
        for i in range(100):
            member = f"scale-person-{i}"
            team = f"scale-team-{i % 20}"
            session.add(MemberRecord(id=member, display_name=f"합성 동명이인 {i % 10}", employment_state="active"))
            session.add(EmploymentPeriodRecord(id=identity("employment", i), member_id=member, state="active", started_at=WHEN))
            session.add(MembershipRecord(id=identity("membership", i), member_id=member, organization_id=team, valid_from=WHEN))
            edge("belongs_to", f"person:{member}", f"team:{team}")
        session.flush()
        for i in range(10):
            owner = "jiho" if i % 2 == 0 else "minseok"
            pid = identity("project", i)
            session.add(ProjectRecord(id=pid, name=f"대량 충돌 2026 프로젝트 {i:02}", created_by_actor_id=owner, created_at=WHEN, updated_at=WHEN))
            session.flush()
            SqlAlchemyProjectRepository(session).add_assignment(project_id=pid, member_id=owner, kind="lead", valid_from=WHEN, valid_until=None, assigned_by=owner)
            edge("assigned_to", f"person:{owner}", f"project:{pid}")
        for i in range(1000):
            requester, assignee = ("mina", "jiho") if i % 2 == 0 else ("hyeon", "minseok")
            rid = identity("request", i)
            session.add(WorkRequestRecord(id=rid, requester_id=requester, assignee_id=assignee, title=f"대량 충돌 2026 요청 {i:04}", state="accepted", created_at=WHEN, updated_at=WHEN))
            edge("requested", f"person:{requester}", f"work_request:{rid}")
            edge("asked_of", f"work_request:{rid}", f"person:{assignee}")
        session.flush()
        for i in range(2000):
            owner = "mina" if i == 1998 else ("jiho" if i % 2 == 0 else "minseok")
            tid = identity("task", i)
            parent = identity("task", i - 1000) if i >= 1500 else None
            source = identity("request", i) if i < 1000 else None
            session.add(TaskRecord(id=tid, created_by_actor_id=owner, title=f"대량 충돌 2026 업무 {i:04}", state="in_progress", organization_unit_id="product" if i % 2 == 0 else "finance", project_id=identity("project", i % 10), parent_task_id=parent, source_work_request_id=source, created_at=WHEN + timedelta(seconds=i), updated_at=WHEN))
            session.add(TaskAssignmentRecord(id=identity("assignment", i), task_id=tid, assignee_id=owner, assignment_kind="self", status="active", created_at=WHEN, accepted_at=WHEN))
            edge("holds", f"person:{owner}", f"task:{tid}")
            edge("part_of", f"task:{tid}", f"project:{identity('project', i % 10)}")
            if source:
                edge("produced", f"work_request:{source}", f"task:{tid}")
            if parent:
                edge("parent_of", f"task:{parent}", f"task:{tid}")
        session.flush()
        for i in range(2000):
            tid = identity("task", i)
            owner = "jiho" if i % 2 == 0 else "minseok"
            for offset in (2, 4):
                target = identity("task", (i + offset) % 2000)
                released = i == 500 and offset == 4
                session.add(TaskReferenceRecord(id=identity(f"reference-{offset}", i), task_id=tid, referenced_task_id=target, created_by=owner, created_at=WHEN, released_at=WHEN if released else None))
                if not released:
                    edge("refers_to", f"task:{tid}", f"task:{target}")
            aid = identity("material", i)
            session.add(AttachmentRecord(id=aid, source_kind="external_link", source_ref=f"https://example.invalid/synthetic/{i}", name=f"대량 근거 {i:04}", content_type="text/uri-list", provenance="synthetic", integrity_ref=f"synthetic:{i}", uploaded_by=owner, created_at=WHEN))
            unbound = i == 1996
            session.add(AttachmentBindingRecord(id=identity("binding", i), attachment_id=aid, context_type="task", context_id=str(tid), role="output", bound_by=owner, bound_at=WHEN, unbound_at=WHEN if unbound else None))
            if not unbound:
                edge("has_material", f"task:{tid}", f"material:{identity('material', i)}")
        for i in range(500):
            owner, attendee = ("jiho", "mina") if i % 2 == 0 else ("minseok", "hyeon")
            mid = identity("meeting", i)
            session.add(MeetingRecord(id=mid, organization_id="scax", owner_id=owner, title=f"대량 충돌 2026 회의 {i:04}", starts_at=WHEN + timedelta(days=i), ends_at=WHEN + timedelta(days=i, hours=1), visibility="private", created_at=WHEN, updated_at=WHEN))
            session.add(MeetingAttendeeRecord(id=identity("attendee", i), meeting_id=mid, member_id=attendee, invited_by=owner, added_at=WHEN))
            edge("owns_meeting", f"person:{owner}", f"meeting:{mid}")
            edge("attended", f"person:{attendee}", f"meeting:{mid}")
        late_meeting = identity("meeting", 498)
        session.add(MeetingAttendeeRecord(id=identity("late-attendee", 0), meeting_id=late_meeting, member_id="hyeon", invited_by="jiho", added_at=WHEN))
        edge("attended", "person:hyeon", f"meeting:{late_meeting}")
        session.commit()
    return {"seed": SEED, "nodes": {"person": 100, "team": 20, "project": 10, "task": 2000, "work_request": 1000, "meeting": 500, "material": 2000}, "edges": edges}
