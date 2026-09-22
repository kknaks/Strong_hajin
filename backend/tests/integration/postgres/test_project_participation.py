from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from sqlalchemy import func, select
from uuid import UUID

from ax_workspace.bootstrap.application import create_workflow_application
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import AccessGrantRecord, ProjectAssignmentRecord, make_session_factory
from ax_workspace.platform.projects import SqlAlchemyProjectRepository
from test_postgres_integration import _postgres_test_url


@pytest.mark.integration
def test_concurrent_duplicate_assignment_keeps_one_active_participation(monkeypatch) -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url)
    first = create_workflow_application(settings)
    second = create_workflow_application(settings)
    project = first.create_project(first.authenticated_principal("jiho"), name="동시 참여 프로젝트")
    project_id = UUID(project["project_id"])
    manager = first.authenticated_principal("jiho")

    both_observed_no_assignment = Barrier(2)
    original = SqlAlchemyProjectRepository.assignment

    def race(self, target_project_id, member_id, *, lock=False):
        found = original(self, target_project_id, member_id, lock=lock)
        if member_id == "hyeon" and found is None:
            both_observed_no_assignment.wait(timeout=10)
        return found

    with monkeypatch.context() as concurrent:
        concurrent.setattr(SqlAlchemyProjectRepository, "assignment", race)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [
                future.result(timeout=15)
                for future in [
                    pool.submit(first.assign_to_project, manager, project_id, "hyeon"),
                    pool.submit(second.assign_to_project, manager, project_id, "hyeon"),
                ]
            ]

    assert results[0]["assignment_id"] == results[1]["assignment_id"]
    with make_session_factory(database_url)() as session:
        active = session.scalar(
            select(func.count())
            .select_from(ProjectAssignmentRecord)
            .where(
                ProjectAssignmentRecord.project_id == project_id,
                ProjectAssignmentRecord.member_id == "hyeon",
                ProjectAssignmentRecord.ended_at.is_(None),
            )
        )
    assert active == 1


@pytest.mark.integration
def test_release_and_rejoin_serialize_without_old_release_revoking_the_new_grant(monkeypatch) -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url)
    first = create_workflow_application(settings)
    second = create_workflow_application(settings)
    project = first.create_project(first.authenticated_principal("jiho"), name="종료 재참여 경합")
    project_id = UUID(project["project_id"])
    manager = first.authenticated_principal("jiho")
    initial = first.assign_to_project(manager, project_id, "hyeon")

    release_flushed = Event()
    rejoin_reached_lock = Event()
    original_assignment = SqlAlchemyProjectRepository.assignment
    original_end = SqlAlchemyProjectRepository.end_assignment

    def observe_rejoin(self, target_project_id, member_id, *, lock=False):
        if member_id == "hyeon" and release_flushed.is_set():
            rejoin_reached_lock.set()
        return original_assignment(self, target_project_id, member_id, lock=lock)

    def pause_release_before_commit(self, assignment, *, ended_by, reason):
        original_end(self, assignment, ended_by=ended_by, reason=reason)
        release_flushed.set()
        assert rejoin_reached_lock.wait(timeout=10)

    with monkeypatch.context() as concurrent:
        concurrent.setattr(SqlAlchemyProjectRepository, "assignment", observe_rejoin)
        concurrent.setattr(SqlAlchemyProjectRepository, "end_assignment", pause_release_before_commit)
        with ThreadPoolExecutor(max_workers=2) as pool:
            released = pool.submit(
                first.release_from_project,
                manager,
                project_id,
                "hyeon",
                reason="교대",
            )
            assert release_flushed.wait(timeout=10)
            rejoined = pool.submit(second.assign_to_project, manager, project_id, "hyeon", kind="lead")
            released.result(timeout=15)
            joined = rejoined.result(timeout=15)

    # 이미 끝난 회차를 다시 종료해도 새 회차와 새 grant는 건드리지 않는다.
    first.release_from_project(
        manager,
        project_id,
        "hyeon",
        assignment_id=UUID(initial["assignment_id"]),
        reason="교대 재전송",
    )

    with make_session_factory(database_url)() as session:
        participations = list(
            session.scalars(
                select(ProjectAssignmentRecord)
                .where(
                    ProjectAssignmentRecord.project_id == project_id,
                    ProjectAssignmentRecord.member_id == "hyeon",
                )
                .order_by(ProjectAssignmentRecord.created_at)
            )
        )
        active_grants = list(
            session.scalars(
                select(AccessGrantRecord).where(
                    AccessGrantRecord.member_id == "hyeon",
                    AccessGrantRecord.scope_ref == str(project_id),
                    AccessGrantRecord.revoked_at.is_(None),
                )
            )
        )

    assert len(participations) == 2
    assert participations[0].ended_at is not None and participations[0].end_reason == "교대"
    assert participations[1].ended_at is None and str(participations[1].id) == joined["assignment_id"]
    assert [grant.origin_project_assignment_id for grant in active_grants] == [participations[1].id]


@pytest.mark.integration
def test_auto_release_then_auto_join_again_does_not_collide_with_the_active_unique_index() -> None:
    """**닫고 다시 붙이는 경로가 `uq_project_assignment_active` 와 부딪히지 않는다** (SPEC-005 §4).

    자동 초대·자동 해제는 **새 제약을 만들지 않는다** — 이미 있는 부분 unique
    (`project_id`,`member_id` WHERE `ended_at IS NULL`)를 그대로 지난다. 그래서 이 시험은
    증명이 아니라 **회귀**다: 떼어진 뒤 같은 사람에게 다시 보내면 **새 회차가 서고**, 닫힌 회차는
    **지워지지 않은 채로 남으며**, 활성 참여는 언제나 하나다.
    """
    database_url = _postgres_test_url()
    reset_database(database_url)
    application = create_workflow_application(Settings(RuntimeProfile.TEST, database_url))
    sender = application.authenticated_principal("yuna")
    project = application.create_project(sender, name="붙었다 떨어지는 프로젝트")
    project_id = UUID(project["project_id"])
    # 프로젝트 범위의 권한은 **붙는 순간에 생긴다** — 그 전에 뜬 principal 은 그것을 모른다.
    sender = application.authenticated_principal("yuna")

    def member_rounds() -> list[tuple]:
        with make_session_factory(database_url)() as session:
            rows = session.scalars(
                select(ProjectAssignmentRecord)
                .where(
                    ProjectAssignmentRecord.project_id == project_id,
                    ProjectAssignmentRecord.member_id == "hyeon",
                )
                .order_by(ProjectAssignmentRecord.created_at)
            ).all()
            return [(row.assignment_kind, row.end_reason, row.ended_at is None) for row in rows]

    first = application.create_work_request(
        sender, "인사 자료 정리", "hyeon", idempotency_key="pg-join-1", project_id=project_id
    )
    assert member_rounds() == [("member", None, True)]

    application.reject_work_request(
        application.authenticated_principal("hyeon"), UUID(first["request_id"]), 1, "지금은 못 맡습니다"
    )
    assert member_rounds() == [("member", "요청 거절", False)]

    # **다시 보내면 새 회차가 선다** — 닫힌 회차를 되살리지도, 유일 제약에 걸리지도 않는다.
    application.create_work_request(
        sender, "채용 공고 검토", "hyeon", idempotency_key="pg-join-2", project_id=project_id
    )
    assert member_rounds() == [("member", "요청 거절", False), ("member", None, True)]

    with make_session_factory(database_url)() as session:
        active = session.scalar(
            select(func.count())
            .select_from(ProjectAssignmentRecord)
            .where(
                ProjectAssignmentRecord.project_id == project_id,
                ProjectAssignmentRecord.member_id == "hyeon",
                ProjectAssignmentRecord.ended_at.is_(None),
            )
        )
    assert active == 1
