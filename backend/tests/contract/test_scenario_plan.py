"""예제 업무 계획은 저장소가 아니라 저장소 밖의 파일이 갖는다.

조직 dataset과 같은 이유다 — 계획은 실제 구성원의 key와 고객사 이름을 가리키므로, 저장소는 그것을 읽는 계약만
갖는다. 계획이 잘못 적혀 있으면 조용히 절반만 만들지 않고 어디가 잘못됐는지 말하고 멈춘다.
"""
import pytest
import yaml

from ax_workspace.bootstrap.scenario import Ask, Gathering, OwnWork, ProjectWork
from ax_workspace.bootstrap.scenario_plan import ScenarioPlanError, load_plan
from ax_workspace.entrypoints.scenario import main

PLAN = {
    "people": {"lead": "m-fixture-lead", "helper": "m-fixture-helper"},
    "own_work": [
        {"owner": "lead", "title": "9월 채용 공고 게시", "description": "직무기술서 확인 후 게시한다.",
         "starts_in": 1, "days": 4, "state": "in_progress", "checklist": ["초안", "게시"]},
    ],
    "project_work": [
        {"owner": "helper", "project": "fixture-project", "items": [["첫 항목", -3, 4], ["둘째 항목", 1, 5]]},
    ],
    "asks": [
        {"requester": "helper", "assignee": "lead", "title": "계약서 검토 요청", "description": "문구를 확인해 주세요.", "due_in": 4},
    ],
    "gatherings": [
        {"owner": "lead", "unit": "fixture-team", "title": "9월 정기 회의", "starts_in": 0, "minutes": 40,
         "attendees": ["lead", "helper"], "note": "[채용] 공고 게시 예정."},
    ],
}


def _written(tmp_path, document, name: str = "scenario.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return path


def test_a_plan_file_becomes_the_same_plan_the_builder_takes(tmp_path) -> None:
    plan = load_plan(_written(tmp_path, PLAN))

    # 사람은 `people`에 적힌 이름으로 부르고, 실제 key는 한 곳에만 적힌다.
    assert plan.own_work == (
        OwnWork("m-fixture-lead", "9월 채용 공고 게시", "직무기술서 확인 후 게시한다.", 1, 4, "in_progress", ("초안", "게시")),
    )
    assert plan.project_work == (
        ProjectWork("m-fixture-helper", "fixture-project", (("첫 항목", -3, 4), ("둘째 항목", 1, 5))),
    )
    # 적지 않은 것은 계약의 기본값으로 남는다 — 요청은 판단을 기다리는 채로 만들어진다.
    assert plan.asks == (Ask("m-fixture-helper", "m-fixture-lead", "계약서 검토 요청", "문구를 확인해 주세요.", 4),)
    assert plan.asks[0].accept is False
    # 참석자처럼 여럿을 가리키는 자리도 같은 이름표를 쓴다.
    assert plan.gatherings == (
        Gathering("m-fixture-lead", "fixture-team", "9월 정기 회의", 0, 40,
                  ("m-fixture-lead", "m-fixture-helper"), "[채용] 공고 게시 예정."),
    )
    assert plan.composite_work == () and plan.handouts == ()


def test_a_name_with_no_entry_is_used_as_the_member_key_itself(tmp_path) -> None:
    """`people`은 편의일 뿐이다. 이름표를 두지 않고 key를 바로 적어도 같다."""
    plan = load_plan(_written(tmp_path, {"own_work": [
        {"owner": "m-direct", "title": "직접", "description": "", "starts_in": 0, "days": 1},
    ]}))
    assert plan.own_work[0].owner == "m-direct"


@pytest.mark.parametrize(
    ("broken", "says"),
    [
        ({"own_work": [{"owner": "a", "title": "t", "description": "", "starts_in": 0}], }, "days"),
        ({"own_work": [{"owner": "a", "title": "t", "description": "", "starts_in": 0, "days": 1, "owener": "a"}], }, "owener"),
        ({"gatherings": {"owner": "a"}}, "gatherings"),
        ({"meetings": []}, "meetings"),
        ({"people": ["a"], "own_work": []}, "people"),
    ],
)
def test_a_plan_that_cannot_be_read_says_where_it_broke(tmp_path, broken, says) -> None:
    with pytest.raises(ScenarioPlanError) as error:
        load_plan(_written(tmp_path, broken))
    assert says in str(error.value)


def test_a_plan_inside_the_repository_is_refused_before_anything_is_built(tmp_path, capsys) -> None:
    """저장소는 계약을 갖고, 실제 사람이 적힌 계획은 갖지 않는다."""
    inside = __import__("pathlib").Path(__file__).resolve().parents[2] / "scenario.yaml"

    assert main([str(inside)]) == 2

    assert "저장소 밖" in capsys.readouterr().err
    assert not inside.exists()
