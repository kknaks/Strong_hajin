from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from ax.settings import Settings


CODEX_BASELINE_PROFILE = {
    "model": "gpt-5.6-terra",
    "service_tier": "priority",
    "conversation_tool_reasoning_effort": "low",
    "workflow_authoring_reasoning_effort": "medium",
    "scope": "MCP protocol baseline only; no cloud model request is made by this probe",
}


def run_protocol_probe(settings: Settings) -> dict[str, Any]:
    """Exercise stdio MCP discovery and a golden flow while recording reproducible local evidence."""
    return asyncio.run(_run_protocol_probe(settings))


async def _run_protocol_probe(settings: Settings) -> dict[str, Any]:
    environment = {
        **os.environ,
        "AX_PROFILE": str(settings.profile),
        "DATABASE_URL": settings.database_url,
        "AX_MCP_PERSONA": "mina",
    }
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ax.mcp_server"],
        cwd=os.getcwd(),
        env=environment,
    )
    measurements: dict[str, float] = {}
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            start = perf_counter()
            tools = await session.list_tools()
            measurements["tool_discovery"] = _milliseconds(start)
            tool_names = [tool.name for tool in tools.tools]

            start = perf_counter()
            started = await session.call_tool(
                "start_daily_report",
                {"input_data": {}},
            )
            measurements["run_start"] = _milliseconds(start)
            run = started.structuredContent or {}

            start = perf_counter()
            completed = await session.call_tool(
                "submit_my_workflow_decision",
                {
                    "run_id": run.get("run_id"),
                    "node_id": "confirm",
                    "decision": "accept",
                    "rationale": "Protocol probe confirmation",
                },
            )
            measurements["decision"] = _milliseconds(start)
            final_run = completed.structuredContent or {}

    expected_tools = {
        "start_daily_report",
        "start_weekly_report",
        "start_customer_visit_report",
        "start_meeting_followups",
        "get_my_workflow_run",
        "get_my_workflow_inbox",
        "submit_my_workflow_decision",
    }
    structured_output = (
        not started.isError
        and isinstance(run.get("run_id"), str)
        and not completed.isError
        and final_run.get("state") == "completed"
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "profile": CODEX_BASELINE_PROFILE,
        "tool_names": tool_names,
        "measurements_ms": {name: round(value, 3) for name, value in measurements.items()},
        "validation": {
            "tool_discovery": expected_tools == set(tool_names),
            "structured_output": structured_output,
            "golden_flow": final_run.get("state") == "completed",
        },
        "run_id": run.get("run_id"),
    }


def _milliseconds(start: float) -> float:
    return (perf_counter() - start) * 1000


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a local MCP protocol baseline.")
    parser.add_argument("--report", type=Path, default=Path("../artifacts/mcp-probe.json"))
    args = parser.parse_args()
    report = run_protocol_probe(Settings.from_environment())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
