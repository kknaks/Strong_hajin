"""예제 업무 계획은 저장소가 아니라 저장소 밖의 CSV가 갖는다.

조직 dataset과 같은 이유이고 같은 모양이다 — 사람이 편집하는 외부 key로 서로를 가리키는 표들이고, 저장소는
읽는 계약만 갖는다. 잘못 적혀 있으면 절반만 만들지 않고 어느 표 몇 번째 줄의 무엇인지 말하고 멈춘다.
"""

import pytest

from ax_workspace.bootstrap.scenario_csv import ScenarioPlanError, load_plan
from scenario_csv_support import make_plan, write_table


def test_the_tables_become_the_plan_the_builder_takes(tmp_path) -> None:
    plan = load_plan(make_plan(tmp_path))

    # 사람은 `scenario_people`에 적힌 이름으로 부르고, 실제 key는 한 곳에만 적힌다.
    assert [(row.key, row.owner, row.state) for row in plan.work] == [
        ("w-1", "m-lead", "in_progress"),
        ("w-2", "m-lead", "open"),
        ("w-3", "m-helper", "open"),
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
    write_table(
        target,
        "scenario_work",
        [
            {
                "key": "w-1",
                "owner": "m-solo",
                "title": "혼자 하는 일",
                "description": "",
                "starts_in": "0",
                "days": "1",
                "state": "",
                "project": "",
                "parent": "",
            }
        ],
    )

    plan = load_plan(target)

    assert len(plan.work) == 1 and plan.asks == () and plan.gatherings == ()
    assert plan.work[0].owner == "m-solo" and plan.work[0].checklist == ()


@pytest.mark.parametrize(
    ("table", "rows", "says"),
    [
        (
            "scenario_work",
            [
                {
                    "key": "w-1",
                    "owner": "m-a",
                    "title": "",
                    "description": "",
                    "starts_in": "0",
                    "days": "1",
                    "state": "",
                    "project": "",
                    "parent": "",
                }
            ],
            "title",
        ),
        (
            "scenario_work",
            [
                {
                    "key": "w-1",
                    "owner": "m-a",
                    "title": "일",
                    "description": "",
                    "starts_in": "곧",
                    "days": "1",
                    "state": "",
                    "project": "",
                    "parent": "",
                }
            ],
            "starts_in",
        ),
        (
            "scenario_work",
            [
                {
                    "key": "w-1",
                    "owner": "m-a",
                    "title": "아이",
                    "description": "",
                    "starts_in": "0",
                    "days": "1",
                    "state": "",
                    "project": "",
                    "parent": "w-2",
                },
                {
                    "key": "w-2",
                    "owner": "m-a",
                    "title": "부모",
                    "description": "",
                    "starts_in": "0",
                    "days": "1",
                    "state": "",
                    "project": "",
                    "parent": "",
                },
            ],
            "뒤에 있습니다",
        ),
        (
            "scenario_checklists",
            [{"owner_kind": "메모", "owner_key": "w-1", "text": "하나"}],
            "owner_kind",
        ),
    ],
)
def test_a_plan_that_cannot_be_read_says_where_it_broke(
    tmp_path, table, rows, says
) -> None:
    target = tmp_path / table
    target.mkdir()
    write_table(target, table, rows)

    with pytest.raises(ScenarioPlanError) as error:
        load_plan(target)

    assert says in str(error.value)
