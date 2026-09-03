"""Run the opt-in real-Codex daily-report smoke against a local SCAX API.

The API owns the Reports operation. This script only creates a real source
Task activity, requests draft generation, then reads the PostgreSQL provenance
records that the operation committed. It never prints a report body or prompt.
"""
from __future__ import annotations

import json
import os
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


API_URL = os.getenv("SCAX_API_URL", "http://127.0.0.1:8001")
DATABASE_URL = os.environ["DATABASE_URL"]
REPORT_DATE = os.getenv("SCAX_REPORT_DATE", "2026-09-03")
HEADERS = {"Content-Type": "application/json", "X-Demo-Persona": "mina"}


def post(path: str, body: dict[str, object]) -> dict[str, object]:
    request = Request(
        f"{API_URL}{path}",
        data=json.dumps(body).encode(),
        headers=HEADERS,
        method="POST",
    )
    with urlopen(request, timeout=180) as response:
        if response.status not in {200, 201}:
            raise RuntimeError(f"{path}: HTTP {response.status}")
        return json.loads(response.read())


def main() -> None:
    task = post("/api/tasks", {"title": "실제 Codex 보고 근거 업무"})
    post(f"/api/tasks/{task['task_id']}/start", {"expected_version": task["version"]})
    draft = post("/api/daily-reports/generate-draft", {"report_date": REPORT_DATE})

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
                "report_id": draft["report_id"],
                "draft_id": draft["draft_id"],
                "workflow_run_id": draft["workflow_run_id"],
                "definition_version_id": draft["definition_version_id"],
                "source_ref_count": len(draft["source_refs"]),
                "workflow_state": draft["workflow_state"],
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
