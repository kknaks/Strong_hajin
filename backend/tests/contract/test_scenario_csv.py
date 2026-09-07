"""예제 업무 계획은 저장소가 아니라 저장소 밖의 CSV가 갖는다.

조직 dataset과 같은 이유이고 같은 모양이다 — 사람이 편집하는 외부 key로 서로를 가리키는 표들이고, 저장소는
읽는 계약만 갖는다. 잘못 적혀 있으면 절반만 만들지 않고 어느 표 몇 번째 줄의 무엇인지 말하고 멈춘다.
"""
import csv

import pytest

from ax_workspace.bootstrap.scenario_csv import TABLES, ScenarioPlanError, initialize, load_plan
from ax_workspace.entrypoints.scenario import main


def _write(target, table: str, rows: list[dict[str, str]]) -> None:
    header = TABLES[table]
    with (target / f"{table}.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(header))
        writer.writeheader()
        writer.writerows(rows)


def _plan(tmp_path):
    """작은 계획 하나: 업무와 그 하위 업무, 참조자가 붙은 요청, 참석자가 있는 회의."""
    target = tmp_path / "dataset"
    target.mkdir()
    initialize(target)
    _write(target, "scenario_people", [{"alias": "lead", "member_key": "m-lead"}, {"alias": "helper", "member_key": "m-helper"}])
    _write(target, "scenario_work", [
        {"key": "w-1", "owner": "lead", "title": "표준화", "description": "표준을 만든다", "starts_in": "0", "days": "20", "state": "in_progress", "project": "", "parent": ""},
        {"key": "w-2", "owner": "lead", "title": "양식 통일", "description": "", "starts_in": "4", "days": "4", "state": "", "project": "", "parent": "w-1"},
        {"key": "w-3", "owner": "helper", "title": "홈페이지 기획", "description": "", "starts_in": "1", "days": "5", "state": "", "project": "p-one", "parent": ""},
    ])
    _write(target, "scenario_checklists", [{"owner_kind": "work", "owner_key": "w-1", "text": "초안"}, {"owner_kind": "request", "owner_key": "r-1", "text": "문구 확인"}])
    _write(target, "scenario_requests", [
        {"key": "r-1", "requester": "helper", "assignee": "lead", "title": "계약서 검토", "description": "봐 주세요", "due_in": "4", "accept": "true"},
    ])
    _write(target, "scenario_request_cc", [{"request_key": "r-1", "member": "m-outsider"}])
    _write(target, "scenario_meetings", [
        {"key": "m-1", "owner": "lead", "unit": "team", "title": "정기 회의", "starts_in": "0", "minutes": "40", "visibility": "", "note": "[채용] 공고 게시.\\n[총무] 비품 정리."},
    ])
    _write(target, "scenario_attendees", [{"meeting_key": "m-1", "member": "helper"}])
    return target


def test_the_tables_become_the_plan_the_builder_takes(tmp_path) -> None:
    plan = load_plan(_plan(tmp_path))

    # 사람은 `scenario_people`에 적힌 이름으로 부르고, 실제 key는 한 곳에만 적힌다.
    assert [(row.key, row.owner, row.state) for row in plan.work] == [
        ("w-1", "m-lead", "in_progress"), ("w-2", "m-lead", "open"), ("w-3", "m-helper", "open"),
    ]
    # 세 가지 업무 모양의 차이는 두 칸뿐이다 — 어느 프로젝트에 매달리는가, 무엇 아래에 들어가는가.
    assert plan.work[1].parent == "w-1" and not plan.work[1].project
    assert plan.work[2].project == "p-one" and not plan.work[2].parent
    assert plan.work[0].checklist == ("초안",)

    [ask] = plan.asks
    assert (ask.requester, ask.assignee, ask.accept) == ("m-helper", "m-lead", True)
    # 이름표가 없는 사람은 적힌 key 그대로 쓴다.
    assert ask.cc == ("m-outsider",) and ask.checklist == ("문구 확인",)

    [meeting] = plan.gatherings
    assert meeting.attendees == ("m-helper",) and meeting.visibility == "public"
    # 회의록의 줄바꿈은 한 칸 안에서 두 글자로 적고, 읽을 때 되돌린다.
    assert meeting.note.splitlines() == ["[채용] 공고 게시.", "[총무] 비품 정리."]


def test_a_table_that_is_not_there_is_an_empty_table(tmp_path) -> None:
    """쓰지 않는 관계까지 파일로 만들라고 요구하지 않는다."""
    target = tmp_path / "sparse"
    target.mkdir()
    _write(target, "scenario_work", [{"key": "w-1", "owner": "m-solo", "title": "혼자 하는 일", "description": "", "starts_in": "0", "days": "1", "state": "", "project": "", "parent": ""}])

    plan = load_plan(target)

    assert len(plan.work) == 1 and plan.asks == () and plan.gatherings == ()
    assert plan.work[0].owner == "m-solo" and plan.work[0].checklist == ()


@pytest.mark.parametrize(
    ("table", "rows", "says"),
    [
        ("scenario_work", [{"key": "w-1", "owner": "m-a", "title": "", "description": "", "starts_in": "0", "days": "1", "state": "", "project": "", "parent": ""}], "title"),
        ("scenario_work", [{"key": "w-1", "owner": "m-a", "title": "일", "description": "", "starts_in": "곧", "days": "1", "state": "", "project": "", "parent": ""}], "starts_in"),
        ("scenario_work", [
            {"key": "w-1", "owner": "m-a", "title": "아이", "description": "", "starts_in": "0", "days": "1", "state": "", "project": "", "parent": "w-2"},
            {"key": "w-2", "owner": "m-a", "title": "부모", "description": "", "starts_in": "0", "days": "1", "state": "", "project": "", "parent": ""},
        ], "뒤에 있습니다"),
        ("scenario_checklists", [{"owner_kind": "메모", "owner_key": "w-1", "text": "하나"}], "owner_kind"),
    ],
)
def test_a_plan_that_cannot_be_read_says_where_it_broke(tmp_path, table, rows, says) -> None:
    target = tmp_path / table
    target.mkdir()
    _write(target, table, rows)

    with pytest.raises(ScenarioPlanError) as error:
        load_plan(target)

    assert says in str(error.value)


def test_a_plan_inside_the_repository_is_refused_before_anything_is_built(tmp_path, capsys) -> None:
    """저장소는 계약을 갖고, 실제 사람과 고객사가 적힌 표는 갖지 않는다."""
    inside = __import__("pathlib").Path(__file__).resolve().parents[2]

    assert main([str(inside)]) == 2

    assert "저장소 밖" in capsys.readouterr().err
