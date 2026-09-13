import ast
import inspect
from types import SimpleNamespace

import pytest

import ax_workspace.modules.actions.policy as policy_module
from ax_workspace.modules.actions.domain import (
    AWAITING_REVIEW,
    AWAITING_REVISION,
    RESOLVED,
    ActionCenterApplication,
    ActionCommand,
    ActionEnvelope,
    ActionError,
)
from ax_workspace.modules.actions.policy import (
    AssignmentActionContext,
    CONFIRM_LABELS,
    AxProposalActionContext,
    DeliveryActionContext,
    WorkRequestActionContext,
    available_assignment_commands,
    available_ax_proposal_commands,
    available_delivery_commands,
    available_work_request_commands,
)
from ax_workspace.modules.organization_access.domain import (
    ACTION_DECIDE,
    TASK_ASSIGN,
    TASK_READ,
    TASK_SELF_MANAGE,
    WORK_REQUEST_DECIDE,
    Principal,
)


def _principal(*capabilities: str, member_id: str = "mina") -> Principal:
    return Principal(
        id=member_id,
        display_name=member_id,
        organization_scope=frozenset(),
        capabilities=frozenset(capabilities),
    )


def _ids(commands) -> list[str]:
    return [command.id for command in commands]


def test_assignment_commands_cover_meaningful_equivalence_classes() -> None:
    cases = [
        ("eligible", True, "mina", (TASK_SELF_MANAGE,), ["accept", "decline"]),
        ("not-pending", False, "mina", (TASK_SELF_MANAGE,), []),
        ("different-assignee", True, "jiho", (TASK_SELF_MANAGE,), []),
        ("missing-capability", True, "mina", (), []),
    ]

    for case, pending, assignee_id, capabilities, expected in cases:
        facts = AssignmentActionContext(pending=pending, assignee_id=assignee_id)
        principal = _principal(*capabilities)

        assert _ids(available_assignment_commands(facts, principal)) == expected, case


def test_assignment_decline_requires_a_reason() -> None:
    commands = available_assignment_commands(
        AssignmentActionContext(pending=True, assignee_id="mina"), _principal(TASK_SELF_MANAGE)
    )

    assert [command.requires_reason for command in commands] == [False, True]


def test_delivery_commands_cover_meaningful_equivalence_classes() -> None:
    cases = [
        ("eligible", True, "mina", (TASK_READ,), ["accept", "request_changes"]),
        ("not-waiting", False, "mina", (TASK_READ,), []),
        ("different-reviewer", True, "jiho", (TASK_READ,), []),
        ("missing-capability", True, "mina", (), []),
    ]

    for case, waiting, reviewer_id, capabilities, expected in cases:
        facts = DeliveryActionContext(waiting=waiting, reviewer_id=reviewer_id)
        principal = _principal(*capabilities)

        assert _ids(available_delivery_commands(facts, principal)) == expected, case


def test_delivery_change_request_requires_a_reason() -> None:
    commands = available_delivery_commands(
        DeliveryActionContext(waiting=True, reviewer_id="mina"), _principal(TASK_READ)
    )

    assert [command.requires_reason for command in commands] == [False, True]


def test_work_request_commands_cover_meaningful_equivalence_classes() -> None:
    cases = [
        (
            "reviewer-can-decide",
            AWAITING_REVIEW,
            "mina",
            (WORK_REQUEST_DECIDE,),
            ["accept", "adjust", "reject"],
        ),
        ("reviewer-missing-capability", AWAITING_REVIEW, "mina", (), []),
        ("different-reviewer", AWAITING_REVIEW, "jiho", (WORK_REQUEST_DECIDE,), []),
        ("requester-revises", AWAITING_REVISION, "mina", (), ["revise", "withdraw"]),
        ("different-requester", AWAITING_REVISION, "jiho", (WORK_REQUEST_DECIDE,), []),
        ("resolved", RESOLVED, "mina", (WORK_REQUEST_DECIDE,), []),
    ]

    for case, status, actor_id, capabilities, expected in cases:
        facts = WorkRequestActionContext(status=status, actor_id=actor_id)
        principal = _principal(*capabilities)

        assert _ids(available_work_request_commands(facts, principal)) == expected, case


def test_work_request_adjust_and_reject_require_reasons() -> None:
    commands = available_work_request_commands(
        WorkRequestActionContext(status=AWAITING_REVIEW, actor_id="mina"),
        _principal(WORK_REQUEST_DECIDE),
    )

    assert [command.requires_reason for command in commands] == [False, True, True]


def _ax_facts(**overrides) -> AxProposalActionContext:
    values = {
        "action_type": "task.create_self",
        "pending": False,
        "resolved": False,
        "has_submission": False,
        "obsolete": False,
        "assignment_status": None,
        "assigned_by": None,
    }
    values.update(overrides)
    return AxProposalActionContext(**values)


def test_ax_proposal_commands_cover_meaningful_equivalence_classes() -> None:
    cases = [
        (
            "confirm-current-submission",
            _ax_facts(pending=True, has_submission=True),
            (ACTION_DECIDE,),
            ["confirm", "reject"],
        ),
        (
            "approve-legacy-proposal",
            _ax_facts(pending=True),
            (ACTION_DECIDE,),
            ["approve", "reject"],
        ),
        (
            "obsolete-can-only-be-rejected",
            _ax_facts(pending=True, has_submission=True, obsolete=True),
            (ACTION_DECIDE,),
            ["reject"],
        ),
        (
            "missing-decision-capability",
            _ax_facts(pending=True, has_submission=True),
            (),
            [],
        ),
        (
            "assigner-cancels-pending-assignment",
            _ax_facts(
                action_type="task.assign",
                resolved=True,
                assignment_status="pending",
                assigned_by="mina",
            ),
            (TASK_ASSIGN,),
            ["cancel_assignment"],
        ),
        (
            "assigner-missing-capability",
            _ax_facts(
                action_type="task.assign",
                resolved=True,
                assignment_status="pending",
                assigned_by="mina",
            ),
            (),
            [],
        ),
        (
            "unresolved-assignment-is-not-cancellable",
            _ax_facts(
                action_type="task.assign",
                assignment_status="pending",
                assigned_by="mina",
            ),
            (TASK_ASSIGN,),
            [],
        ),
        (
            "different-assigner",
            _ax_facts(
                action_type="task.assign",
                resolved=True,
                assignment_status="pending",
                assigned_by="jiho",
            ),
            (TASK_ASSIGN,),
            [],
        ),
        (
            "active-assignment-is-not-cancellable",
            _ax_facts(
                action_type="task.assign",
                resolved=True,
                assignment_status="active",
                assigned_by="mina",
            ),
            (TASK_ASSIGN,),
            [],
        ),
        (
            "non-assignment-proposal",
            _ax_facts(resolved=True, assignment_status="pending", assigned_by="mina"),
            (TASK_ASSIGN,),
            [],
        ),
    ]

    for case, facts, capabilities, expected in cases:
        principal = _principal(*capabilities)

        assert _ids(available_ax_proposal_commands(facts, principal)) == expected, case


@pytest.mark.parametrize("action_type, label", CONFIRM_LABELS.items())
def test_ax_proposal_confirm_labels_are_canonical(action_type: str, label: str) -> None:
    commands = available_ax_proposal_commands(
        AxProposalActionContext(
            action_type=action_type,
            pending=True,
            resolved=False,
            has_submission=True,
            obsolete=False,
            assignment_status=None,
            assigned_by=None,
        ),
        _principal(ACTION_DECIDE),
    )

    assert commands[0].label == label


def test_ax_proposal_can_forbid_reject_during_required_recovery() -> None:
    commands = available_ax_proposal_commands(
        _ax_facts(pending=True, has_submission=True, allow_reject=False),
        _principal(ACTION_DECIDE),
    )

    assert _ids(commands) == ["confirm"]


class _Handler:
    def __init__(self, commands: list[ActionCommand]) -> None:
        self.commands = commands
        self.executed = False

    def find(self, action_item_id: str):
        return SimpleNamespace(id=action_item_id)

    def envelope(self, item, principal: Principal) -> ActionEnvelope:
        return ActionEnvelope(
            action_item_id=str(item.id),
            kind="test",
            status=AWAITING_REVIEW,
            subject="판단",
            operation_label="테스트",
            current_question="결정하세요",
            preview=[],
            allowed_commands=self.commands,
            submission_version=1,
            waiting_on=None,
            resource={"type": "test", "id": str(item.id)},
        )

    def is_replay(
        self, item, principal: Principal, command: str, payload: dict
    ) -> bool:
        return False

    def normalize(self, item, command: str, payload: dict) -> dict:
        return payload

    def execute(self, principal: Principal, item, command: str, payload: dict) -> None:
        self.executed = True


def test_action_center_refuses_a_command_the_policy_did_not_offer() -> None:
    handler = _Handler([ActionCommand("accept", "수락")])

    with pytest.raises(ActionError, match="is not available"):
        ActionCenterApplication([handler]).execute(_principal(), "item-1", "reject", {})
    assert handler.executed is False


def test_action_center_requires_a_reason_when_the_policy_marks_it_required() -> None:
    handler = _Handler([ActionCommand("reject", "거절", requires_reason=True)])

    with pytest.raises(ActionError, match="requires a reason"):
        ActionCenterApplication([handler]).execute(
            _principal(), "item-1", "reject", {"reason": "  "}
        )
    assert handler.executed is False


def test_action_policy_has_no_adapter_or_sqlalchemy_dependency() -> None:
    tree = ast.parse(inspect.getsource(policy_module))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert all(
        not module.startswith("ax_workspace.platform") for module in imported_modules
    )
    assert all(not module.startswith("sqlalchemy") for module in imported_modules)
