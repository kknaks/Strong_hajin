"""프로젝트 — 부서를 가로질러 묶이는 일과, 그 일을 함께 하는 사람들.

조직 단위가 사람이 어디에 속하는지를 말한다면, 프로젝트는 사람들이 무엇을 함께 하는지를 말한다. 둘은 나란히 선다:
프로젝트에 붙는다고 조직 안에서 갖던 것이 줄지 않고, 프로젝트 밖의 일이 열리지도 않는다.

소유 조직 단위를 두지 않는다. 마케팅 한 건에 국내사업부 AE와 비주얼디자인팀 디자이너가 함께 붙는 것이 정상이고,
그것이 이 모듈이 있는 이유다 — 어느 한 부서의 것이라고 적으면 그 부서가 프로젝트를 여는 열쇠가 된다.

이 모듈은 권한을 스스로 만들지 않는다. 배정이 무슨 권한을 부르는지는 조직·권한 모듈의 표준 규칙이 답하며,
여기서는 배정이라는 사실만 기록한다.
"""
from __future__ import annotations

from ax_workspace.modules.work.project_results import ProjectView, ProjectDetailResult, ProjectAssignmentView, ProjectParticipationView, ProjectAssignmentHistoryView, ProjectTaskView

# **기간 정규화도 상태 투영도 이 모듈이 다시 쓰지 않는다** (SPEC-005 §4 · 어긋남 ①).
# 같은 판정이 두 벌이 되면 같은 업무가 두 표면에서 다른 값으로 읽힌다 — 그것이 고치는 자리다.
from ax_workspace.modules.work.lifecycle import TaskState
from ax_workspace.modules.work.schedule import task_span
from ax_workspace.modules.work.task_projection import external_state, overdue_days, today_for_tasks

from ax_workspace.modules.errors import ResourceNotFound

from datetime import UTC, date, datetime
from typing import Any, Protocol
from uuid import UUID
from ax_workspace.modules.work.project_commands import ProjectCreateInput, ProjectMemberInput, ProjectReleaseInput, ProjectReleaseResult

from ax_workspace.modules.organization_access.domain import (
    PROJECT_MANAGE,
    PROJECT_READ,
    TASK_ASSIGN,
    Principal,
)


class ProjectError(Exception):
    pass


class ProjectNotFound(ProjectError, ResourceNotFound):
    pass


class ProjectAccessDenied(ProjectError):
    pass


class ProjectRepository(Protocol):
    def create(
        self,
        *,
        name: str,
        description: str | None,
        starts_on: date | None,
        ends_on: date | None,
        external_key: str | None,
        created_by: str,
    ) -> Any: ...
    def project(self, project_id: UUID) -> Any | None: ...
    def by_external_key(self, external_key: str) -> Any | None: ...
    def all_projects(self) -> list[Any]: ...
    def assignments_for(self, project_id: UUID) -> list[Any]: ...
    def assignment_history(self, project_id: UUID) -> list[Any]: ...
    def assignment(self, project_id: UUID, member_id: str, *, lock: bool = False) -> Any | None: ...
    def assignment_round(
        self,
        project_id: UUID,
        member_id: str,
        assignment_id: UUID,
        *,
        lock: bool = False,
    ) -> Any | None: ...
    def add_assignment(self, *, project_id: UUID, member_id: str, kind: str, valid_from: datetime | None, valid_until: datetime | None, assigned_by: str) -> Any: ...
    def end_assignment(self, assignment: Any, *, ended_by: str, reason: str | None) -> None: ...
    def member_projects(self, member_id: str) -> list[Any]: ...
    def tasks_in(self, project_id: UUID) -> list[Any]: ...
    def member_names(self, member_ids: list[str]) -> dict[str, str]: ...


ASSIGNMENT_KINDS = ("lead", "member")

#: 자동 해제가 참여 이력에 남기는 사유 — **둘뿐이다** (SPEC-005 §4 · D-13).
#: 배정 쪽 거절은 「요청 거절」을, 배정 쪽 철회는 「요청 철회」를 **그대로 쓴다**. 셋째를 만들면
#: 참여 이력의 어휘가 넷으로 갈린다.
PROJECT_END_REQUEST_REJECTED = "요청 거절"
PROJECT_END_REQUEST_WITHDRAWN = "요청 철회"


def auto_joined_by(assignment: Any) -> bool:
    """**조건 ① — 「이 배정이 그 사람을 새로 붙였나」를 읽는 한 자리** (SPEC-005 §4 · D-14).

    떼는 자리가 넷이고 그 넷이 전부 이 사실을 물으므로, **읽는 코드도 한 자리여야 한다.**

    **기본값 폴백을 두지 않는다.** `getattr(assignment, "auto_project_join", False)` 로 읽으면
    그 칸이 사라지거나 이름이 바뀐 날 **오류 하나 없이 「안 붙였다」로 읽혀 떼는 자리 넷이 전부
    조용히 무동작**이 된다 — 자동 해제가 통째로 없어지는데 테스트도 로그도 아무 말을 안 한다.
    그래서 여기서는 **직접 속성 접근**으로 읽는다: 칸이 없으면 `AttributeError` 로 시끄럽게 깨진다.

    `None` 만 거짓으로 접는다 — **배정 행이 아직 없는 것**(요청 쪽에서 일어난다)은 「안 붙였다」가
    맞고, 그것은 칸이 사라진 것과 다른 사실이다.
    """
    if assignment is None:
        return False
    return bool(assignment.auto_project_join)


class ProjectTaskFactSource(Protocol):
    """프로젝트 상세의 업무 줄이 **함께 낼 사실들** — 활성 선행 · 체크리스트 집계 · 담당.

    **프로젝트 저장소가 아니라 업무 저장소가 갖는다** — 선행은 업무와 업무의 관계이고, 담당은 배정
    행이 답하며, 체크리스트는 업무의 자식이다. 프로젝트는 그 사실들의 범위일 뿐이다. 여기서 포트로
    받는 이유가 그것이다: 같은 질의가 업무 목록과 프로젝트 상세에서 두 벌이 되면 한쪽만 고쳤을 때
    두 화면이 서로 다른 것을 그린다 (SPEC-001 U-15 · SPEC-005 §4).

    **셋 다 「업무마다 묻지 않는」 모양이다** — 업무 id 를 통째로 받아 한 번에 낸다.
    """

    def predecessors_for(self, task_ids: list[UUID]) -> dict[UUID, list[UUID]]: ...
    #: 업무 상세·업무 목록이 쓰는 **바로 그 집계**다. 두 곳에서 세면 두 수가 된다 (D-01).
    def checklist_progress_for(self, task_ids: list[UUID]) -> dict[UUID, tuple[int, int]]: ...
    #: 「지금 누가 드는가 · 누가 답을 기다리나」의 원장 사실. 담당 투영은 이 값에서만 나온다 (D-08).
    def origin_facts(self, tasks: list[Any]) -> dict[UUID, dict[str, Any]]: ...


class ProjectApplication:
    """프로젝트를 만들고, 사람을 붙이고, 읽는다. 읽을 수 있는 범위는 언제나 grant가 답한다."""

    def __init__(self, repository: ProjectRepository, task_facts: "ProjectTaskFactSource | None" = None) -> None:
        self._repository = repository
        self._task_facts = task_facts

    # ---- commands ----

    def create(
        self,
        principal: Principal,
        *,
        name: str,
        description: str | None = None,
        starts_on: date | None = None,
        ends_on: date | None = None,
        external_key: str | None = None,
    ) -> ProjectView:
        """프로젝트 하나. 만들고, 사람을 붙인다 — 그 둘이 프로젝트의 전부다.

        소유 조직을 두지 않는다. 프로젝트는 부서를 가로질러 묶이려고 있는 것이라 어느 한 부서의 것이라고
        말하는 순간 그 부서가 열쇠가 되고, 붙어야 보인다는 규칙에 뒷문이 생긴다.

        만든 사람은 담당자로 함께 기록된다. 그러지 않으면 만든 사람조차 자기 프로젝트를 찾지 못해 아무도
        붙일 수 없고, 프로젝트는 만들어지자마자 고아가 된다.
        """
        if PROJECT_MANAGE not in principal.capabilities:
            raise ProjectAccessDenied("프로젝트를 만들 수 있는 자격이 없습니다")
        command = ProjectCreateInput(name=name, description=description, starts_on=starts_on, ends_on=ends_on, external_key=external_key)
        name, description, starts_on, ends_on, external_key = command.name, command.description, command.starts_on, command.ends_on, command.external_key
        cleaned = " ".join(str(name or "").split())
        if not cleaned:
            raise ProjectError("프로젝트 이름이 필요합니다")
        if starts_on and ends_on and ends_on < starts_on:
            raise ProjectError("끝나는 날이 시작하는 날보다 앞설 수 없습니다")
        if external_key and self._repository.by_external_key(external_key) is not None:
            raise ProjectError("이미 있는 프로젝트 key입니다")
        project = self._repository.create(
            name=cleaned,
            description=(description or "").strip() or None,
            starts_on=starts_on,
            ends_on=ends_on,
            external_key=external_key,
            created_by=str(principal.id),
        )
        self._repository.add_assignment(
            project_id=project.id,
            member_id=str(principal.id),
            kind="lead",
            valid_from=None,
            valid_until=None,
            assigned_by=str(principal.id),
        )
        return self._view(project)

    def assign(
        self,
        principal: Principal,
        project_id: UUID,
        member_id: str,
        *,
        kind: str = "member",
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
    ) -> ProjectAssignmentView:
        """사람을 붙인다. 그 사람이 어느 부서인지는 묻지 않는다 — 그것이 프로젝트가 있는 이유다."""
        project = self._manageable(principal, project_id)
        command = ProjectMemberInput(member_id=member_id, kind=kind, valid_from=valid_from, valid_until=valid_until)
        member_id, kind, valid_from, valid_until = command.member_id, command.kind, command.valid_from, command.valid_until
        if kind not in ASSIGNMENT_KINDS:
            raise ProjectError("배정 종류는 담당 또는 참여입니다")
        if valid_from and valid_until and valid_until < valid_from:
            raise ProjectError("끝나는 날이 시작하는 날보다 앞설 수 없습니다")
        existing = self._repository.assignment(project.id, member_id, lock=True)
        if existing is not None:
            return self._assignment_view(existing)
        assignment = self._repository.add_assignment(
            project_id=project.id,
            member_id=member_id,
            kind=kind,
            valid_from=valid_from,
            valid_until=valid_until,
            assigned_by=str(principal.id),
        )
        return self._assignment_view(assignment)

    # ---- 업무 명령의 부수 효과로 오가는 자리 — 사람이 직접 쓰는 문(`assign`·`release`)이 아니다 ----

    def join_for_assignment(self, principal: Principal, project_id: UUID, member_id: str) -> bool:
        """**업무를 배정·발송할 때 받는 사람을 그 프로젝트에 붙인다** (SPEC-005 §4 · D-11 · D-28).

        **열쇠가 없다 — 배정이 성립한 것이 곧 조건이다** (D-28, 사용자 결정 · 2026-09-22).
        **배정한 사람의 권한을 여기서 다시 판정하지 않는다.**

        **~~앞 판의 열쇠는 「그 프로젝트에서의 업무 배정 권한」이었다 (D-12)~~.** 그것은
        **프로젝트 축의 판정**이었고 **조직 축 권한으로 배정하는 팀장이 거기서 탈락했다** — 그러면
        받는 사람은 **일은 맡았는데 그 일이 도는 프로젝트를 못 본다.** D-11 이 고치려던 증상이
        그 자리에 그대로 남아서 게이트를 뺐다.

        **막아서 지킬 것이 없다** — 초대가 주는 것은 **읽기뿐**이고 관리 권한도 쓰기도 딸려오지
        않는다. **그리고 배정 자체의 권한 검사는 그대로 산다**: 배정이 막히면 **여기에 닿기 전에**
        막힌다.

        ⚠ **함께 움직이지 않는 것이 있다.** 「배정 권한 없는 사람은 그 프로젝트에 업무를 **올릴** 수
        없다」는 규칙은 **그대로다**(`assignments.plan_project_work()`). 뺀 것은 **「붙이기」의 게이트
        하나**이고, 둘을 같이 빼면 **프로젝트에 업무를 올리는 문이 열린다.**

        붙는 관계는 **`참여`(member)** 다. `lead` 로 붙이지도 승격하지도 않는다.
        **거절 갈래를 만들지 않는다** — 여기서는 **거절이 아니라 무동작**이다. 이미 붙어 있어도
        아무 일도 일어나지 않는다(멱등).

        돌려주는 값은 **「이 명령이 새로 붙였나」** 하나다. 그 사실은 붙이는 그 순간에만 설 수 있어서
        (나중에 보면 원래 멤버와 구별되지 않는다) 부르는 쪽이 배정 행에 적는다 (D-14 조건 ①).
        """
        project = self._repository.project(project_id)
        if project is None:
            return False
        if self._repository.assignment(project.id, member_id, lock=True) is not None:
            return False
        self._repository.add_assignment(
            project_id=project.id,
            member_id=member_id,
            kind="member",
            valid_from=None,
            valid_until=None,
            assigned_by=str(principal.id),
        )
        return True

    def release_for_assignment(
        self,
        project_id: UUID | None,
        member_id: str | None,
        *,
        auto_joined: bool,
        except_task_id: UUID | None,
        reason: str,
        ended_by: str,
    ) -> bool:
        """**거절·철회로 붙은 근거가 사라졌을 때 참여를 닫는다** (SPEC-005 §4 · D-13·D-14).

        **조건 둘이 다 참일 때만 뗀다.** 그 판정이 여기 한 곳에 있다 — 떼는 자리가 넷이라
        네 곳에서 각자 판정하면 한 곳만 고쳐졌을 때 문에 따라 다르게 동작한다.

        | 조건 | 무엇 | 왜 |
        |---|---|---|
        | ① | **이 배정이 실제로 새로 붙였을 때만** | 원래 멤버를 남의 거절로 떼어내지 않는다 |
        | ② | **그 사람의 다른 활성 업무가 그 프로젝트에 없을 때만** | 하나만 거절돼도 그 사람은 **아직 그 프로젝트 일을 하고 있다**(S-7) |

        **행을 지우지 않는다** — 「붙었다가 거절로 떨어졌다」도 일어난 일이고, 지우면 참여 이력이
        거짓이 된다. `ended_at` 과 사유만 남는다.

        **관리 권한을 묻지 않는다.** 사람이 누른 「떼기」가 아니라 **그 사람 자신의 거절·철회가
        되돌리는 것**이라 `release()` 와 다른 문이다.

        **거절이 아니라 무동작이다** — 조건이 안 맞으면 새 오류 갈래 없이 `False` 를 돌려준다.
        """
        if not auto_joined or project_id is None or not member_id:
            return False
        if self._holds_other_active_work(project_id, member_id, except_task_id=except_task_id):
            return False
        assignment = self._repository.assignment(project_id, member_id, lock=True)
        if assignment is None or assignment.ended_at is not None:
            return False
        self._repository.end_assignment(assignment, ended_by=ended_by, reason=reason)
        return True

    def _holds_other_active_work(self, project_id: UUID, member_id: str, *, except_task_id: UUID | None) -> bool:
        """**조건 ②** — 그 사람이 이 프로젝트에 다른 활성 업무를 들고 있나 (D-14).

        **취소된 업무는 세지 않는다** — 그것은 이미 없어진 일이다. 그 밖에는 들고 있는 것도
        답을 기다리는 것도 **센다**: 「그 일이 있었나」가 가르는 선이므로(D-15) 수락 뒤 완료된 일도
        그 사람이 이 프로젝트에 있었다는 사실로 남는다.

        **새 질의를 만들지 않는다** — 프로젝트 상세가 쓰는 `tasks_in()` 과 업무 목록이 쓰는
        `origin_facts()` 를 그대로 지난다. 담당 판정이 두 벌이 되면 조용히 갈린다.

        ⚠ **동시성 틈이 남는다.** 같은 사람에게 보낸 업무 둘이 **동시에** 거절되면 둘 다 상대의
        활성 업무를 보고 **아무도 안 뗀다.** 결과는 「사람이 프로젝트에 남는 것」이고 그것은
        **참여가 잘못 사라지는 것보다 안전한 쪽**이다. 「한 사람 한 참여」와 달리 이 세기는
        두 표에 걸쳐 있어 **DB 제약으로 올릴 수 없다** — 감추지 않고 여기 적는다.
        """
        if self._task_facts is None:
            return False
        others = [
            task
            for task in self._repository.tasks_in(project_id)
            if task.id != except_task_id and str(task.state) != TaskState.CANCELLED.value
        ]
        if not others:
            return False
        facts = self._task_facts.origin_facts(others)
        return any(
            member_id in {str(fact.get("assignee_id") or ""), str(fact.get("pending_assignee_id") or "")}
            for fact in facts.values()
        )

    def release(
        self,
        principal: Principal,
        project_id: UUID,
        member_id: str,
        *,
        assignment_id: UUID | None = None,
        reason: str | None = None,
    ) -> ProjectReleaseResult:
        """사람을 뗀다. 그와 함께 그 프로젝트로 얻었던 권한도 끝난다."""
        project = self._manageable(principal, project_id)
        command = ProjectReleaseInput(assignment_id=assignment_id, reason=reason)
        assignment_id, reason = command.assignment_id, command.reason
        assignment = (
            self._repository.assignment_round(project.id, member_id, assignment_id, lock=True)
            if assignment_id is not None
            else self._repository.assignment(project.id, member_id, lock=True)
        )
        if assignment is None:
            raise ProjectNotFound("이 프로젝트에 배정된 구성원이 아닙니다")
        # 회차를 지정한 종료 요청은 그 회차의 receipt다. 종료 뒤 재전송되어도 새 회차를 대신 끝내지 않는다.
        receipt: ProjectReleaseResult = {"project_id": str(project.id), "member_id": member_id, "assignment_id": str(assignment.id), "released": True}
        if assignment.ended_at is not None:
            return receipt
        self._repository.end_assignment(
            assignment,
            ended_by=str(principal.id),
            reason=(reason or "").strip() or None,
        )
        return receipt

    # ---- queries ----

    def list(self, principal: Principal) -> list[ProjectView]:
        """이 사람이 읽을 수 있는 프로젝트만. 읽을 수 없는 프로젝트는 개수로도 드러나지 않는다."""
        if PROJECT_READ not in principal.capabilities:
            raise ProjectAccessDenied("프로젝트를 조회할 수 있는 자격이 없습니다")
        reach = self._readable(principal)
        return [
            {**self._view(project)}
            for project in self._repository.all_projects()
            if str(project.id) in reach
        ]

    def get(self, principal: Principal, project_id: UUID) -> ProjectDetailResult:
        project = self._readable_project(principal, project_id)
        assignments = self._repository.assignments_for(project.id)
        names = self._repository.member_names([row.member_id for row in assignments])
        tasks = self._repository.tasks_in(project.id)
        # **새 조회를 만들지 않는다** (SPEC-001 §4 · U-15). 업무 줄이 선행·담당·체크리스트 집계를
        # 함께 내고, 화면은 그 배열 하나로 간트와 좌 레일을 그린다. **줄마다 따로 묻지 않는다** —
        # `tasks_in()` 은 필터도 페이징도 없이 그 프로젝트의 업무 전부를 내므로(D-16 의 전제),
        # 줄마다 물으면 업무 수만큼 질의가 된다 (SPEC-005 §4).
        task_ids = [task.id for task in tasks]
        preceding = self._task_facts.predecessors_for(task_ids) if self._task_facts is not None else {}
        progress = self._task_facts.checklist_progress_for(task_ids) if self._task_facts is not None else {}
        facts = self._task_facts.origin_facts(list(tasks)) if self._task_facts is not None else {}
        # 담당의 표시 이름은 **참여자 이름과 같은 명부**에서 온다 — 한 번에 묻는다.
        assignee_ids = sorted({member_id for member_id in (self._assignee_id(facts.get(task.id, {})) for task in tasks) if member_id})
        assignee_names = self._repository.member_names(assignee_ids) if assignee_ids else {}
        # **「오늘」은 한 번만 판정한다** — 줄마다 다시 읽으면 자정을 걸친 한 응답이 두 날을 섞는다.
        today = today_for_tasks()
        return {
            **self._view(project),
            # 무엇을 할 수 있는지는 서버가 말한다. 화면이 권한을 추측해 버튼을 그리면 눌러야 아는 거절이 된다.
            "may_manage": principal.allows(PROJECT_MANAGE, project=str(project.id)),
            "members": [
                {**self._assignment_view(row), "display_name": names.get(row.member_id, row.member_id)}
                for row in assignments
            ],
            "tasks": [
                self._task_view(
                    task,
                    preceding=preceding.get(task.id, []),
                    progress=progress.get(task.id, (0, 0)),
                    fact=facts.get(task.id, {}),
                    names=assignee_names,
                    today=today,
                )
                for task in tasks
            ],
        }

    @staticmethod
    def _assignee_id(fact: dict[str, Any]) -> str | None:
        """**담당은 활성 배정이 답한다** (D-08) — 업무 상세의 `_assignment_view()` 와 같은 말이다.

        `active` 를 먼저 찾고, 없으면(수락 대기·첫 지정) 답을 기다리는 행이 답한다. 둘 다 없으면
        **아무도 들지 않은 것**이고 그것이 정상이다 — 「미정」을 지어내지 않는다.
        """
        holder = fact.get("assignee_id") or fact.get("pending_assignee_id")
        return str(holder) if holder else None

    def _task_view(
        self,
        task: Any,
        *,
        preceding: list[UUID],
        progress: tuple[int, int],
        fact: dict[str, Any],
        names: dict[str, str],
        today: date,
    ) -> ProjectTaskView:
        """프로젝트 상세의 업무 한 줄. **화면의 모든 칸이 이 배열에서 나온다** (SPEC-005 §4).

        **싣지 않는 것도 계약이다**: 하위 집계·후행 배열·「분류」·저장된 진행률·승인 값은 없다.
        화면이 세거나(하위·후행) 만들지 않기로 한 것(진행률 저장)이라 서버가 또 내면 원천이 둘이 된다.
        """
        done, total = progress
        # **기간 정규화는 `task_span()` 하나다** (SPEC-004 증보 K14 · D-09). 한쪽만이면 그 날 하루,
        # 뒤집혔으면 `[min, max]`, 둘 다 없으면 기간 없음 — 화면이 그 규칙을 다시 쓰지 않는다.
        span = task_span(task.start_date, task.due_date)
        member_id = self._assignee_id(fact)
        return {
            "task_id": str(task.id),
            "title": task.title,
            # **투영을 지난다** — 여기가 어긋남 ① 을 닫는 자리다 (SPEC-005 §2.9).
            "state": external_state(task.state),
            "start_date": task.start_date.isoformat() if task.start_date else None,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "parent_task_id": str(task.parent_task_id) if task.parent_task_id else None,
            "preceding_task_ids": [str(item) for item in preceding],
            "assignee": (
                {"member_id": member_id, "display_name": names.get(member_id, member_id)}
                if member_id
                else None
            ),
            "checklist_progress": {"done": done, "total": total},
            "span_from": span.span_from.isoformat() if span is not None else None,
            "span_to": span.span_to.isoformat() if span is not None else None,
            # **투영 앞의 원값으로 판정한다** — `completion_submitted` 도 끝난 쪽이라 값이 없다.
            "overdue_days": overdue_days(task.due_date, task.state, today),
        }

    def participation_history(self, principal: Principal, project_id: UUID) -> list[ProjectParticipationView]:
        project = self._readable_project(principal, project_id)
        rows = self._repository.assignment_history(project.id)
        names = self._repository.member_names(
            sorted(
                {
                    member_id
                    for row in rows
                    for member_id in (row.member_id, row.assigned_by_member_id, row.ended_by_member_id)
                    if member_id
                }
            )
        )
        return [
            {
                **self._assignment_history_view(row),
                "display_name": names.get(row.member_id, row.member_id),
                "assigned_by_display_name": names.get(row.assigned_by_member_id) if row.assigned_by_member_id else None,
                "ended_by_display_name": names.get(row.ended_by_member_id) if row.ended_by_member_id else None,
            }
            for row in rows
        ]

    def readable_project_ids(self, principal: Principal) -> frozenset[str]:
        """업무 읽기가 물어보는 것: 이 사람의 프로젝트 범위가 닿는 프로젝트들."""
        return self._readable(principal)

    def assignable_members(self, principal: Principal) -> list[dict[str, str]]:
        """이 사람이 배정할 수 있는 프로젝트의 사람들. 어느 부서인지는 묻지 않는다.

        배정 권한이 닿는 프로젝트에 함께 붙어 있는 사람들이며, 자기 자신은 빼고 돌려준다. 조직 축에서 오는
        후보와 합치는 일은 배정 모듈이 한 곳에서 한다 — 여기서는 프로젝트가 아는 것만 말한다.
        """
        reach = principal.projects_for(TASK_ASSIGN) if TASK_ASSIGN in principal.capabilities else frozenset()
        if not reach:
            return []
        found: dict[str, str] = {}
        for project_id in sorted(reach):
            for row in self._repository.assignments_for(UUID(project_id)):
                if row.member_id == str(principal.id) or row.member_id in found:
                    continue
                found[row.member_id] = ""
        names = self._repository.member_names(sorted(found))
        return [{"id": member_id, "display_name": names.get(member_id, member_id)} for member_id in sorted(found)]

    def may_assign_in(self, principal: Principal, project_id: UUID) -> bool:
        return principal.allows(TASK_ASSIGN, project=str(project_id))

    # ---- internals ----

    def _readable(self, principal: Principal) -> frozenset[str]:
        """읽을 수 있는 프로젝트 — 붙어 있는 것뿐이다.

        축이 하나면 뒷문이 없다. 부서로도, 만든 사람이라는 사실로도 열리지 않고, 배정이라는 한 가지 사실로만
        열린다. 조직 전체를 읽는 자격은 그 자격이 따로 말한다.
        """
        if PROJECT_READ not in principal.capabilities:
            return frozenset()
        return frozenset(principal.projects_for(PROJECT_READ))

    def _readable_project(self, principal: Principal, project_id: UUID) -> Any:
        project = self._repository.project(project_id)
        # 읽을 수 없는 프로젝트는 없는 것과 같이 답한다. 있다는 사실 자체가 알려지지 않는다.
        if project is None or str(project.id) not in self._readable(principal):
            raise ProjectNotFound("프로젝트를 찾을 수 없습니다")
        return project

    def _manageable(self, principal: Principal, project_id: UUID) -> Any:
        project = self._repository.project(project_id)
        if project is None:
            raise ProjectNotFound("프로젝트를 찾을 수 없습니다")
        if not principal.allows(PROJECT_MANAGE, project=str(project.id)):
            raise ProjectAccessDenied("이 프로젝트를 관리할 수 있는 자격이 없습니다")
        return project

    @staticmethod
    def _view(project: Any) -> ProjectView:
        return {
            "project_id": str(project.id),
            "name": project.name,
            "description": project.description,
            "state": project.state,
            "starts_on": project.starts_on.isoformat() if project.starts_on else None,
            "ends_on": project.ends_on.isoformat() if project.ends_on else None,
            "external_key": project.external_key,
            "version": project.version,
        }

    @staticmethod
    def _assignment_view(assignment: Any) -> ProjectAssignmentView:
        def utc(value: datetime | None) -> str | None:
            # SQLite returns stored UTC timestamps without tzinfo; never reinterpret
            # those wall-clock values in the host's local timezone.
            if value is None:
                return None
            return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).isoformat()

        return {
            "assignment_id": str(assignment.id),
            "member_id": assignment.member_id,
            "assignment_kind": assignment.assignment_kind,
            # 유효기간은 비어 있을 수 있다. 비어 있으면 지금부터 계속이라는 뜻이다.
            "valid_from": utc(assignment.valid_from),
            "valid_until": utc(assignment.valid_until),
        }

    @classmethod
    def _assignment_history_view(cls, assignment: Any) -> ProjectAssignmentHistoryView:
        def utc(value: datetime | None) -> str | None:
            if value is None:
                return None
            return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).isoformat()

        return {
            **cls._assignment_view(assignment),
            "assigned_by_member_id": assignment.assigned_by_member_id,
            "created_at": utc(assignment.created_at),
            "ended_at": utc(assignment.ended_at),
            "ended_by_member_id": assignment.ended_by_member_id,
            "end_reason": assignment.end_reason,
        }
