"""Projects, current participation and closed participation rounds are separate views."""
from typing_extensions import TypedDict


class ProjectView(TypedDict):
    project_id: str
    name: str
    description: str | None
    state: str
    starts_on: str | None
    ends_on: str | None
    external_key: str | None
    version: int


class ProjectAssignmentView(TypedDict):
    assignment_id: str
    member_id: str
    assignment_kind: str
    valid_from: str | None
    valid_until: str | None


class ProjectMemberView(ProjectAssignmentView):
    display_name: str


class ProjectTaskAssigneeView(TypedDict):
    """**지금 이 업무를 든 사람** — 업무의 열이 아니라 활성 배정이 답한다 (SPEC-005 §4 · D-08).

    아무도 들지 않은 업무가 **정상**이라 `None` 으로 온다. 「미정」 같은 말을 서버가 지어내지 않는다.
    """

    member_id: str
    display_name: str


class ProjectTaskProgressView(TypedDict):
    """체크리스트 **두 수만**. 항목은 싣지 않는다 (SPEC-005 §4 · D-01·D-02).

    `total` 이 0 이면 화면이 % 를 **그리지 않는다** — 0% 가 아니다. 서버는 그 판정을 하지 않고
    두 수만 낸다.
    """

    done: int
    total: int


class ProjectTaskView(TypedDict):
    task_id: str
    title: str
    #: **밖으로 나가는 상태는 계약의 넷뿐**이다 (SPEC-005 §2.9 · 어긋남 ①). `completion_submitted` 는
    #: 투영이 `done` 으로 접어 새지 않는다 — 업무 상세가 내는 값과 같은 값이다.
    state: str
    start_date: str | None
    due_date: str | None
    parent_task_id: str | None
    #: **간트 연결선의 유일한 원천** (SPEC-001 U-15 · §4). 이 줄이 함께 내므로 선행을 묻는 조회를
    #: 따로 만들지 않는다. 상위–하위는 다른 그림이라 `parent_task_id` 와 섞지 않는다.
    preceding_task_ids: list[str]
    #: **담당.** 좌 레일 카드의 「분류」를 뺀 자리에 선다 (D-08).
    assignee: ProjectTaskAssigneeView | None
    #: 간트 바의 % 와 우 레일 미터의 재료. **저장된 진행률이 아니다** (D-01).
    checklist_progress: ProjectTaskProgressView
    #: **정규화된 기간** — SPEC-004 와 같은 이름·같은 규칙(`task_span()`). 한쪽만/뒤집힘/없음을 서버가
    #: 이미 접어서 낸다. 화면이 다시 계산하지 않는다 (D-09).
    span_from: str | None
    span_to: str | None
    #: 기한이 며칠 지났는가. **끝난 업무(완료·취소·승인 대기)에는 값이 없다** — 화면이 「오늘」을
    #: 따로 판정하지 않고 **값이 온 업무만 센다** (SPEC-001 §U-3 승계).
    overdue_days: int | None


class ProjectDetailResult(ProjectView):
    may_manage: bool
    members: list[ProjectMemberView]
    tasks: list[ProjectTaskView]


class ProjectAssignmentHistoryView(ProjectAssignmentView):
    assigned_by_member_id: str | None
    created_at: str | None
    ended_at: str | None
    ended_by_member_id: str | None
    end_reason: str | None


class ProjectParticipationView(ProjectAssignmentHistoryView):
    display_name: str
    assigned_by_display_name: str | None
    ended_by_display_name: str | None
