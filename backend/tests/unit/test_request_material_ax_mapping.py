"""요청 자료 업로드가 **AX 에서 판단 근거로 새지 않는다** (WORK-003 리뷰 F-1).

`file_attachment_request` 는 intent 로 대상을 가른다. 그 목록에 `request_material` 이 없는 동안
work_request 대상의 업로드는 `request_comment_attachment` 가 아니면 **전부 `add_evidence` 로 떨어진다**
(`bootstrap/browser_interactions.py::SessionBrowserFileTargets.upload`). 그래서 「요청 자료를 붙인다」고
적힌 HTTP 행을 그 도구에 `verified` 로 걸면, 계약서에는 자료인데 원장에는 판단 근거가 남는다.

이 시험은 **둘을 함께 묶는다**: intent 가 없으면 그 행은 verified 가 아니어야 하고, intent 가 생기면
이 시험이 그때 실패해서 행을 되돌리라고 말한다. 한쪽만 고치고 다른 쪽을 잊는 길을 닫는다.
"""
import json
from pathlib import Path
from typing import get_args

from ax_workspace.modules.ax_execution.browser_interactions import BrowserFileRequest

ROOT = Path(__file__).resolve().parents[3]
UPLOAD_ROUTE = "POST /api/work-requests/{request_id}/materials"


def _intents() -> frozenset[str]:
    return frozenset(get_args(BrowserFileRequest.model_fields["intent"].annotation))


def _row(http: str) -> dict:
    inventory = json.loads((ROOT / "docs/unified-operations-inventory.json").read_text())
    rows = [*inventory["http"], *inventory["current_runtime"]["added_operations"]]
    [row] = [item for item in rows if item["http"] == http]
    return row


def test_the_request_material_upload_is_not_verified_while_ax_would_route_it_to_evidence() -> None:
    row = _row(UPLOAD_ROUTE)
    if "request_material" in _intents():
        # intent 가 생겼다 — 이 행을 verified 로 되돌리고 target_tools 를 다시 걸 차례다.
        assert row["status"] == "verified" and row["target_tools"], (
            "request_material intent 가 생겼으면 요청 자료 업로드 행을 verified 로 되돌려야 한다"
        )
        return
    assert row["status"] == "excluded", (
        "request_material intent 가 없는 동안 이 행이 verified 면 AX 는 자료 대신 판단 근거를 붙인다"
    )
    assert row["target_tools"] == []
    assert row["exclusion"]["reconsider_when"]
    # 실제로 그 자리가 판단 근거로 떨어진다는 사실 자체를 함께 못 박는다.
    assert {"request_evidence", "request_comment_attachment"} <= _intents()
