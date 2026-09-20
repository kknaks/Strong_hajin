"""Disposable My Work fixtures, invoked only by the reset-demo command.

Mina is the main browser persona; Jiho sends requests and reviews her reports.
Dates follow reset day. All writes use existing commands and real demo actors.
OQ-203/OQ-206 remain open: no unfinished children or meeting promotions are
used to manufacture an approval policy. Ordinary requesters review deliveries.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from ax_workspace.bootstrap.application import WorkflowApplication
from ax_workspace.bootstrap.scenario import Ask, ScenarioPlan, Work, build
from ax_workspace.modules.work.application import TaskState


DEMO_WORK = ScenarioPlan(
    work=(
        Work("demo-progress", "mina", "제품 사용성 조사 정리", starts_in=-1, days=3, state="in_progress"),
        Work("demo-blocked", "mina", "연동 규격 확인", starts_in=-2, days=5, state="in_progress"),
        Work("demo-overdue", "mina", "지난주 고객 의견 분류", starts_in=-5, days=3, state="in_progress"),
        Work("demo-calendar", "mina", "오늘 데모 점검", starts_in=0, days=0),
        Work("demo-done", "mina", "제품 안내 문구 정리", starts_in=-3, days=2, state="done"),
    ),
    asks=(
        Ask("jiho", "mina", "다음 배포 안내 검토 요청", "검토 범위와 일정을 확인해 주세요.", 4),
        Ask("mina", "jiho", "제품 지표 검토 요청", "지표 선정 의견을 부탁드립니다.", 3),
        Ask("jiho", "mina", "사용성 조사 결과 보고", "조사 결과와 개선안을 정리해 주세요.", 2, accept=True),
        Ask("jiho", "mina", "배포 체크리스트 보완", "배포 전 점검 항목을 정리해 주세요.", 1, accept=True),
        Ask("mina", "jiho", "지표 정의 결과 확인", "정의한 지표의 결과를 공유해 주세요.", 2, accept=True),
        Ask("yuna", "jiho", "제품 운영 점검 참조", "민아도 진행 상황을 참고해 주세요.", 3, cc=("mina",)),
        Ask("yuna", "jiho", "분기 계획 검토 참조", "수락한 요청의 처리 결과를 공유합니다.", 4, accept=True, cc=("mina",)),
    ),
)


def seed_demo_work(application: WorkflowApplication, *, today: date | None = None) -> None:
    """Run after a fresh reset; fail loudly if a fixture no longer fits the contract."""
    result = build(application, DEMO_WORK, today=today)
    if result.skipped:
        raise RuntimeError(f"Demo work seed incomplete: {result.skipped}")

    mina = application.authenticated_principal("mina")
    blocked = next(row for row in application.list_tasks(mina) if row["title"] == "연동 규격 확인")
    application.transition_task(
        UUID(blocked["task_id"]), mina, TaskState.BLOCKED,
        reason="외부 연동 규격 회신을 기다리고 있습니다.", expected_version=blocked["version"],
    )

    for requester_id, holder_id, title, revision in (
        ("jiho", "mina", "사용성 조사 결과 보고", False),
        ("jiho", "mina", "배포 체크리스트 보완", True),
        ("mina", "jiho", "지표 정의 결과 확인", False),
    ):
        holder = application.authenticated_principal(holder_id)
        requester = application.authenticated_principal(requester_id)
        task = next(row for row in application.list_tasks(holder) if row["title"] == title)
        task_id = UUID(task["task_id"])
        started = application.transition_task(task_id, holder, TaskState.IN_PROGRESS, expected_version=task["version"])
        application.submit_task_completion(holder, task_id, started["version"], summary="1차 검토 결과를 정리했습니다.")
        if revision:
            item = next(
                row for row in application.pending_action_items(requester)
                if row["kind"] == "task.delivery" and row["resource"]["id"] == str(task_id)
            )
            application.run_action_command(requester, item["action_item_id"], "request_changes", {
                "expected_version": item["expected_version"], "reason": "실패 시 복구 절차를 추가해 주세요.",
            })
