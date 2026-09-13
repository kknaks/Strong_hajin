"""Run the opt-in real-Codex daily-report smoke against a local SCAX API.

The API owns the Reports operation. This script only creates a real source
Task activity, requests draft generation, then reads the PostgreSQL provenance
records that the operation committed. It never prints a report body or prompt.
"""
from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from urllib.request import Request, urlopen
from uuid import UUID

from sqlalchemy import select

from ax_workspace.platform.persistence import (
    ProviderCallRecord,
    ReportDraftRecord,
    WorkflowNodeExecutionRecord,
    WorkflowRunRecord,
    make_session_factory,
)
from ax_workspace.platform.work_tasks import business_date


API_URL = os.getenv("SCAX_API_URL", "http://127.0.0.1:8001")
DATABASE_URL = os.environ["DATABASE_URL"]
REPORT_DATE = os.getenv("SCAX_REPORT_DATE") or business_date(datetime.now(UTC))
HEADERS = {"Content-Type": "application/json", "X-Demo-Persona": "mina"}


def post(path: str, body: dict[str, object]) -> dict[str, object]:
    request = Request(
        f"{API_URL}{path}",
        data=json.dumps(body).encode(),
        headers=HEADERS,
        method="POST",
    )
    with urlopen(request, timeout=180) as response:
        if response.status not in {200, 201, 202}:
            raise RuntimeError(f"{path}: HTTP {response.status}")
        return json.loads(response.read())


def get(path: str) -> dict[str, object]:
    request = Request(f"{API_URL}{path}", headers={"X-Demo-Persona": "mina"})
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"{path}: HTTP {response.status}")
        return json.loads(response.read())


def main() -> None:
    task = post("/api/tasks", {"title": "실제 Codex 보고 근거 업무"})
    started_task = post(
        f"/api/tasks/{task['task_id']}/start", {"expected_version": task["version"]}
    )
    accepted = post("/api/daily-reports/generate-draft", {"report_date": REPORT_DATE})
    deadline = time.monotonic() + 180
    while True:
        status = get(f"/api/daily-reports/status?report_date={REPORT_DATE}")
        if status["generation_id"] != accepted["generation_id"]:
            raise RuntimeError("daily-report status returned a different generation")
        if status["generation_status"] == "completed" and status["report_id"]:
            break
        if status["generation_status"] in {"failed", "needs_verification"}:
            raise RuntimeError(
                f"daily-report generation ended as {status['generation_status']}: "
                f"{status['generation_error_code']}"
            )
        if time.monotonic() >= deadline:
            raise RuntimeError("daily-report generation did not finish within 180 seconds")
        time.sleep(1)
    history = get(f"/api/daily-reports/{status['report_id']}/history")
    draft = history["drafts"][-1]
    matching_sources = [
        source
        for source in draft["source_refs"]
        if source.get("task_id") == started_task["task_id"]
        and source.get("task_version") == started_task["version"]
        and source.get("state") == started_task["state"]
    ]
    assert len(matching_sources) == 1, (
        "generated report did not retain the exact task/version/state created for this smoke"
    )

    with make_session_factory(DATABASE_URL)() as session:
        report_draft = session.get(ReportDraftRecord, UUID(str(draft["draft_id"])))
        run = session.get(WorkflowRunRecord, UUID(str(draft["workflow_run_id"])))
        assert report_draft is not None
        assert run is not None
        node_runs = list(
            session.scalars(
                select(WorkflowNodeExecutionRecord)
                .where(WorkflowNodeExecutionRecord.run_id == run.id)
                .order_by(WorkflowNodeExecutionRecord.created_at)
            )
        )
        provider_call = session.scalar(
            select(ProviderCallRecord)
            .join(WorkflowNodeExecutionRecord)
            .where(WorkflowNodeExecutionRecord.run_id == run.id)
        )

        assert report_draft.workflow_run_id == run.id
        assert report_draft.definition_version_id == run.definition_version_id
        assert run.state == "completed"
        assert [node.node_id for node in node_runs] == ["sources", "render", "generate", "validate"]
        assert provider_call is not None and provider_call.status == "completed"
        assert provider_call.provider_run_ref or provider_call.provider_session_ref
        assert provider_call.requested_model == "gpt-5.6-terra"

    print(
        json.dumps(
            {
                "report_id": status["report_id"],
                "draft_id": draft["draft_id"],
                "workflow_run_id": draft["workflow_run_id"],
                "definition_version_id": draft["definition_version_id"],
                "source_ref_count": len(draft["source_refs"]),
                "generation_id": accepted["generation_id"],
                "generation_status": status["generation_status"],
                "node_runs": [node.node_id for node in node_runs],
                "provider_status": provider_call.status,
                "provider_run_ref_present": bool(provider_call.provider_run_ref),
                "provider_session_ref_present": bool(provider_call.provider_session_ref),
                "requested_model": provider_call.requested_model,
                "observed_model": provider_call.observed_model,
                "requested_tier": provider_call.requested_tier,
                "observed_tier": provider_call.observed_tier,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
