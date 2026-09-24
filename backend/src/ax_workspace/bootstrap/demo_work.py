"""Disposable My Work fixtures, invoked only by the reset-demo command.

Mina is the main browser persona; Jiho sends requests and reviews her reports.
Dates follow reset day. All writes use existing commands and real demo actors.
OQ-203/OQ-206 remain open: no unfinished children or meeting promotions are
used to manufacture an approval policy. Ordinary requesters review deliveries.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from ax_workspace.bootstrap.application import WorkflowApplication
from ax_workspace.bootstrap.scenario import Ask, ScenarioPlan, Work, build
from ax_workspace.modules.organization_access.domain import Principal
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
    day = today or datetime.now(UTC).date()
    result = build(application, DEMO_WORK, today=day)
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

    _seed_demo_projects(application, today=day)


# ---------------------------------------------------------------------------
# 「프로젝트」 화면의 더미 — 화면의 어려운 자리가 전부 한 번씩 선다.
#
# 위의 시드는 업무·캘린더·수신함이 쓰므로 **건드리지 않는다.** 여기서는 더하기만 한다.
# 모든 줄이 제품 자신의 명령을 그 일을 했을 사람으로 지난다 — 3층 트리도 **요청 발송 → 수락**의
# 실제 경로로 선다(직접 배정은 프로젝트를 싣지 못한다). 날짜는 전부 `today` 기준 상대값이다.
# ---------------------------------------------------------------------------

#: 프로젝트를 여는 사람. 지호는 팀장이라 `project.manage` 를 갖는다 — 민아(구성원)는 못 만든다.
PROJECT_OWNER = "jiho"

REDESIGN_KEY = "demo-project-redesign"
BRAND_KEY = "demo-project-brand"


@dataclass(frozen=True, slots=True)
class ProjectWork:
    """프로젝트 안의 업무 한 줄.

    스스로 든 일 · 요청으로 선 일 · 아직 아무도 안 든 계획을 세 표로 두지 않는다. 셋의 차이는
    `requested_by` 와 `unheld` 뿐이고 나머지는 같은 업무다 — `scenario.Work` 가 `project`·`parent`
    하나로 가르는 것과 같은 결이다.
    """

    key: str
    owner: str
    title: str
    #: `today` 기준 상대 일수. `None` 이면 그 날짜가 **없다** — 한쪽만/둘 다 없음이 화면의 갈래다.
    starts_in: int | None = None
    due_in: int | None = None
    state: str = "open"
    #: 이 표 안에서 앞서 나온 업무의 key — 상위 업무.
    parent: str = ""
    #: 이 표 안에서 앞서 나온 업무의 key 들 — 선행 업무.
    after: tuple[str, ...] = ()
    checklist: tuple[str, ...] = ()
    #: 앞에서부터 몇 개를 체크했나. 체크리스트가 있고 **일부만** 끝난 업무가 막대에 fill 과 % 를 낸다.
    checked: int = 0
    #: 요청으로 서는 줄이면 보낸 사람. 발송 → 수락의 실제 경로를 지난다.
    requested_by: str = ""
    #: 아무도 들지 않은 계획 — `plan_project_work` 가 그 문이다. 화면이 「미정」을 지어내지 않는 것을 본다.
    unheld: bool = False
    #: 사유를 요구하는 두 전이(`blocked`·`cancelled`)가 쓴다. 나머지 상태에서는 비어 있다.
    reason: str = ""


#: 「하반기 제품 개편」 — 트리·의존선·진행률·기간·상태가 전부 여기 산다.
REDESIGN_WORK: tuple[ProjectWork, ...] = (
    # E 시작·마감이 다 있다 · D 완료된 업무 · C 두 선의 출발점
    ProjectWork("redesign-research", "mina", "사용자 리서치 설문", starts_in=-21, due_in=-14, state="done"),
    # C 평범한 선 ① 최상위 → 최상위
    ProjectWork("redesign-scope", "mina", "개편 범위 확정", starts_in=-12, due_in=4, state="in_progress", after=("redesign-research",)),
    # C 평범한 선 ② · E 마감만 있다 — 그 날 하루로 접혀 온다
    ProjectWork("redesign-compare", "mina", "경쟁 제품 비교", due_in=6, after=("redesign-scope",)),
    # E 기한이 지났다 — 지연 칸에 잡힌다
    ProjectWork("redesign-login", "mina", "로그인 화면 개선", starts_in=-10, due_in=-3, state="in_progress"),
    # F blocked — 사유가 필수다
    ProjectWork(
        "redesign-payment", "mina", "결제 연동 점검", starts_in=-4, due_in=9, state="blocked",
        reason="결제사 테스트 계정 발급을 기다리고 있습니다.",
    ),
    # F cancelled 하나 — 취소선 바, 요약 모수에서 빠진다
    ProjectWork(
        "redesign-legacy", "mina", "구형 브라우저 대응", starts_in=-6, due_in=2, state="cancelled",
        reason="지원 대상에서 빠져 더 하지 않기로 했습니다.",
    ),
    # F 담당이 없다(`assignee: null`) · E 기간이 아예 없다 — 간트에 안 서고 좌 레일에만
    ProjectWork("redesign-a11y", PROJECT_OWNER, "접근성 점검", unheld=True),
    # B 3층 트리의 최상위 · D 하위를 가진 상위 업무
    ProjectWork("redesign-report", "mina", "보고서 작성", starts_in=-2, due_in=12, state="in_progress"),
    # B 1단계 — 민아가 지호에게 **요청**하고 지호가 **수락**한다 · D 하위를 가진 상위 업무
    ProjectWork(
        "redesign-report-design", "jiho", "보고서 디자인", starts_in=-1, due_in=9, state="in_progress",
        parent="redesign-report", requested_by="mina",
    ),
    # B 2단계 = 손자 — 지호가 자기 하위로 쪼갠다. 프로젝트는 **상속**으로 들어간다
    # C 손자의 선행이 **다른 가지**에 있다 — 접으면 닻이 접힌 부모 바에 붙는다
    # D 체크리스트 일부 완료 — 막대에 fill 과 %
    ProjectWork(
        "redesign-survey", "jiho", "디자인 시안 조사", starts_in=0, due_in=5,
        parent="redesign-report-design", after=("redesign-research",),
        checklist=("경쟁 서비스 화면 수집", "내부 자료 정리", "톤앤매너 후보 3안", "레퍼런스 코멘트", "요약 정리"),
        checked=2,
    ),
    # C **같은 접힌 가지 안쪽** 선 — 형제끼리. 접으면 선이 아니라 건수로 나온다
    # D 체크리스트가 없다 — % 도 fill 도 안 난다(0% 가 아니다)
    ProjectWork(
        "redesign-shortlist", "jiho", "시안 후보 정리", starts_in=3, due_in=8,
        parent="redesign-report-design", after=("redesign-survey",),
    ),
)


def _seed_demo_projects(application: WorkflowApplication, *, today: date) -> None:
    """프로젝트 둘과 그 안의 업무. 실패하면 조용히 넘기지 않고 그대로 터진다 — 빈 화면보다 낫다."""
    owner = application.authenticated_principal(PROJECT_OWNER)
    redesign = application.create_project(
        owner,
        name="하반기 제품 개편",
        description="제품 화면과 흐름을 하반기 안에 한 번 정리한다.",
        starts_on=today - timedelta(days=21),
        ends_on=today + timedelta(days=45),
        external_key=REDESIGN_KEY,
    )
    # **업무가 하나도 없는 프로젝트** — 빈 본문과 빈 간트가 어떻게 서는지 본다.
    brand = application.create_project(
        owner,
        name="브랜드 리뉴얼",
        description="아직 일이 올라오지 않은 프로젝트.",
        starts_on=today + timedelta(days=7),
        ends_on=today + timedelta(days=90),
        external_key=BRAND_KEY,
    )
    # **만든 뒤 다시 읽는다.** 만든 사람은 `lead` 로 붙지만 그 프로젝트 범위의 grant 는 방금 생겼고,
    # 손에 든 principal 은 그 전에 읽은 것이라 아직 그 프로젝트를 모른다 — 붙이는 명령이 거절된다.
    owner = application.authenticated_principal(PROJECT_OWNER)
    # **민아는 한쪽에 담당(`lead`), 다른 쪽에 참여(`member`)로 붙는다.** 그러면 셀렉터를 바꾸는 것만으로
    # 「관리 권한이 있는 화면」과 「없는 화면」을 둘 다 볼 수 있다 — SPEC-005 E-17 이 후자를 요구한다.
    application.assign_to_project(owner, UUID(str(redesign["project_id"])), "mina", kind="lead")
    owner = application.authenticated_principal(PROJECT_OWNER)
    application.assign_to_project(owner, UUID(str(brand["project_id"])), "mina", kind="member")

    project_id = UUID(str(redesign["project_id"]))
    made: dict[str, dict[str, Any]] = {}

    for item in REDESIGN_WORK:
        start_date = today + timedelta(days=item.starts_in) if item.starts_in is not None else None
        due_date = today + timedelta(days=item.due_in) if item.due_in is not None else None
        parent_id = UUID(str(made[item.parent]["task_id"])) if item.parent else None
        preceding = [UUID(str(made[key]["task_id"])) for key in item.after]

        if item.unheld:
            # 아무도 들지 않은 계획. 배정 행이 하나도 없다는 것이 곧 「담당자 미정」이다.
            made[item.key] = application.plan_project_work(
                owner, project_id, item.title,
                description="담당자가 아직 정해지지 않은 계획 줄.",
                start_date=start_date, due_date=due_date,
            )
            continue

        holder = application.authenticated_principal(item.owner)
        if item.requested_by:
            # **요청 발송 → 수락.** 직접 배정으로 흉내내면 프로젝트가 안 실린다 (SPEC-003 §4).
            requester = application.authenticated_principal(item.requested_by)
            sent = application.create_work_request(
                requester, item.title, item.owner,
                idempotency_key=f"demo:project-request:{item.key}",
                description="요청으로 선 업무다 — 받는 사람이 수락해 담당이 섰다.",
                start_date=start_date, due_date=due_date,
                project_id=project_id, parent_task_id=parent_id,
                preceding_task_ids=preceding or None,
                checklist=list(item.checklist) or None,
            )
            application.accept_work_request(holder, UUID(str(sent["request_id"])), int(sent["version"]))
            made[item.key] = next(
                row for row in application.list_tasks(holder, include_closed=True) if row["title"] == item.title
            )
        else:
            made[item.key] = application.create_task(
                holder, item.title,
                idempotency_key=f"demo:project-work:{item.key}",
                start_date=start_date, due_date=due_date,
                # **하위는 프로젝트를 묻지 않고 상위를 따른다** — 손자가 상속으로 들어가는 자리다.
                project_id=None if parent_id is not None else project_id,
                parent_task_id=parent_id,
                preceding_task_ids=preceding or None,
                checklist=list(item.checklist) or None,
            )

        task_id = UUID(str(made[item.key]["task_id"]))
        version = int(made[item.key]["version"])
        if item.checked:
            version = _check_off(application, holder, task_id, item.checked)
        if item.state != "open":
            version = _walk_state(application, holder, task_id, version, item.state, item.reason)
        made[item.key] = {**made[item.key], "version": version}


def _check_off(application: WorkflowApplication, holder: Principal, task_id: UUID, count: int) -> int:
    """앞에서부터 `count` 개를 체크한다 — 그 사람이 실제로 누른다. 누가 언제 눌렀는지가 그래서 남는다."""
    detail = application.get_task(holder, task_id)
    version = int(detail["version"])
    for item in detail["checklist"][:count]:
        moved = application.update_task_checklist_item(
            holder, task_id, UUID(str(item["item_id"])), done=True, expected_version=int(item["version"]),
        )
        version = int(moved.get("task_version", version))
    return version


def _walk_state(
    application: WorkflowApplication, holder: Principal, task_id: UUID, version: int, target: str, reason: str
) -> int:
    """상태는 옮겨 적는 것이 아니라 그 사람이 실제로 옮긴다 — 이력도 그렇게 남는다.

    **사유를 요구하는 전이가 둘이다** — 막힘과 취소. 마지막 걸음에만 싣는다.
    """
    for step in {
        "in_progress": (TaskState.IN_PROGRESS,),
        "done": (TaskState.IN_PROGRESS, TaskState.DONE),
        "blocked": (TaskState.IN_PROGRESS, TaskState.BLOCKED),
        "cancelled": (TaskState.CANCELLED,),
    }[target]:
        moved = application.transition_task(
            task_id, holder, step,
            reason=reason if step in {TaskState.BLOCKED, TaskState.CANCELLED} else None,
            expected_version=version,
        )
        version = int(moved["version"])
    return version
