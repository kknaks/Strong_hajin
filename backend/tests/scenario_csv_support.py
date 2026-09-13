import csv

from ax_workspace.bootstrap.scenario_csv import TABLES, initialize


def write_table(target, table: str, rows: list[dict[str, str]]) -> None:
    header = TABLES[table]
    with (target / f"{table}.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(header))
        writer.writeheader()
        writer.writerows(rows)


def make_plan(tmp_path):
    """Build a small plan with nested work, a request, and a meeting."""
    target = tmp_path / "dataset"
    target.mkdir()
    initialize(target)
    write_table(
        target,
        "scenario_people",
        [
            {"alias": "lead", "member_key": "m-lead"},
            {"alias": "helper", "member_key": "m-helper"},
        ],
    )
    write_table(
        target,
        "scenario_work",
        [
            {
                "key": "w-1",
                "owner": "lead",
                "title": "표준화",
                "description": "표준을 만든다",
                "starts_in": "0",
                "days": "20",
                "state": "in_progress",
                "project": "",
                "parent": "",
            },
            {
                "key": "w-2",
                "owner": "lead",
                "title": "양식 통일",
                "description": "",
                "starts_in": "4",
                "days": "4",
                "state": "",
                "project": "",
                "parent": "w-1",
            },
            {
                "key": "w-3",
                "owner": "helper",
                "title": "홈페이지 기획",
                "description": "",
                "starts_in": "1",
                "days": "5",
                "state": "",
                "project": "p-one",
                "parent": "",
            },
        ],
    )
    write_table(
        target,
        "scenario_checklists",
        [
            {"owner_kind": "work", "owner_key": "w-1", "text": "초안"},
            {"owner_kind": "request", "owner_key": "r-1", "text": "문구 확인"},
        ],
    )
    write_table(
        target,
        "scenario_requests",
        [
            {
                "key": "r-1",
                "requester": "helper",
                "assignee": "lead",
                "title": "계약서 검토",
                "description": "봐 주세요",
                "due_in": "4",
                "accept": "true",
            },
        ],
    )
    write_table(
        target, "scenario_request_cc", [{"request_key": "r-1", "member": "m-outsider"}]
    )
    write_table(
        target,
        "scenario_meetings",
        [
            {
                "key": "m-1",
                "owner": "lead",
                "unit": "team",
                "title": "정기 회의",
                "starts_in": "0",
                "minutes": "40",
                "visibility": "",
                "note": "[채용] 공고 게시.\\n[총무] 비품 정리.",
            },
        ],
    )
    write_table(
        target, "scenario_attendees", [{"meeting_key": "m-1", "member": "helper"}]
    )
    return target
