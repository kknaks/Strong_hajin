from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, TypeVar
from uuid import UUID

from ax_workspace.modules.organization_access.domain import seeded_principal
from ax_workspace.platform.persistence import make_session_factory
from ax_workspace.platform.workflow_runtime import SqlAlchemyUnitOfWork, workflow_service
from ax_workspace.modules.ax_execution.application import WorkflowRunStarter
from ax_workspace.bootstrap.settings import Settings


T = TypeVar("T")


def _operation(settings: Settings, action: Callable[[WorkflowRunStarter], T]) -> T:
    with SqlAlchemyUnitOfWork(make_session_factory(settings.database_url)) as uow:
        assert uow.workflows is not None
        return action(workflow_service(uow.workflows))


def run_golden_rehearsal(settings: Settings) -> dict[str, Any]:
    """Run the three domain-complete demos against an already explicit-reset database."""
    if not settings.developer_auth_enabled:
        raise RuntimeError("The local demo rehearsal requires development or test profile")

    mina = seeded_principal("mina")
    admin = seeded_principal("demo-admin")
    sora = seeded_principal("sora")
    minseok = seeded_principal("minseok")

    daily_started = _operation(settings, lambda service: service.start("daily-report", mina, {}))
    if "daily_report.submit_snapshot" in {item["tool_name"] for item in daily_started["tool_results"]}:
        raise AssertionError("Daily report submitted before the human confirmation")
    daily = _operation(
        settings,
        lambda service: service.decide(
            UUID(daily_started["run_id"]), "confirm", mina, "accept", "Demo operator confirmed the draft", {}
        ),
    )

    meeting_started = _operation(settings, lambda service: service.start("meeting-followups", mina, {}))
    meeting_requested = _operation(
        settings,
        lambda service: service.decide(
            UUID(meeting_started["run_id"]), "choose-assignment", mina, "accept", "Assign to demo operator", {"assignee_id": "mina"}
        ),
    )
    my_work_before = _operation(settings, lambda service: service.my_work(mina))
    if any(item["run_id"] == meeting_started["run_id"] for item in my_work_before):
        raise AssertionError("Assignment entered My Work before assignee acceptance")
    meeting = _operation(
        settings,
        lambda service: service.decide(
            UUID(meeting_started["run_id"]), "accept-assignment", mina, "accept", "I accept this task", {}
        ),
    )
    my_work_after = _operation(settings, lambda service: service.my_work(mina))

    contract_started = _operation(
        settings, lambda service: service.start("contract-review", admin, {"contract_id": "SCAX-DEMO-001"})
    )
    contract_after_legal = _operation(
        settings,
        lambda service: service.decide(
            UUID(contract_started["run_id"]), "legal", sora, "accept", "Legal review accepted", {}
        ),
    )
    effect_after_legal = "contract.approve" in {
        item["tool_name"] for item in contract_after_legal["tool_results"]
    }
    if effect_after_legal:
        raise AssertionError("Contract effect ran before the finance all-join decision")
    contract = _operation(
        settings,
        lambda service: service.decide(
            UUID(contract_started["run_id"]), "finance", minseok, "accept", "Finance review accepted", {}
        ),
    )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "flows": {
            "daily_report": _flow_summary(daily),
            "meeting_followups": {
                **_flow_summary(meeting),
                "my_work_state": my_work_after[0]["state"] if my_work_after else "missing",
                "request_state": meeting_requested["state"],
            },
            "contract_review": {**_flow_summary(contract), "effect_after_legal": effect_after_legal},
        },
    }


def _flow_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run["run_id"],
        "state": run["state"],
        "tool_names": [item["tool_name"] for item in run["tool_results"]],
        "audit_events": [item["event_type"] for item in run["audit"]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the three SCAX golden workflow demos.")
    parser.add_argument("--report", type=Path, default=Path("../artifacts/demo-rehearsal.json"))
    args = parser.parse_args()
    report = run_golden_rehearsal(Settings.from_environment())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
