"""끝냈다와 완료로 인정한다는 다른 사실이다.

When work was asked for by someone, the person doing it does not get to declare the request satisfied. They report
what they delivered — frozen as it was at that moment — and the person who asked answers a question of their own:
accept it, or say what is still missing. Asking for more is not a new question; it is another round of the same one.
"""
from uuid import UUID
import pytest

from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import SubmissionRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
SORA = {"X-Demo-Persona": "sora"}



def _accept(client, request: dict, headers=JIHO) -> None:
    """받는 사람이 수락한다 — 여기서 담당이 확정되고 그 업무가 「내 업무」에 선다.

    W1 에서는 이 단계가 없었다(발송이 곧 배정). v2 가 되돌린 것은 **이 한 단계뿐**이고,
    아래 테스트들이 보는 관계·이력·완료는 그대로다.
    """
    answered = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers=headers, json={"expected_version": request["version"]},
    )
    assert answered.status_code == 200, answered.text

def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return TestClient(app), database_url


def _requested_task(client, title: str = "요청한 업무") -> str:
    """Mina asks and Jiho starts: the shape every delivery review begins from.

    수락 단계는 없어졌지만 완료 승인은 그대로다 — 업무가 `source_work_request_id` 로 요청을 가리키고,
    확인자를 그 요청의 요청자에게서 찾는다 (WORK-001 § 최소 호환 경계).
    """
    request = client.post("/api/work-requests", headers=MINA, json={"title": title, "assignee_id": "jiho"}).json()
    # v2: 발송은 업무를 세우고 **담당은 수락이 세운다** (SPEC-003 §4). 그 뒤는 예전과 같다.
    _accept(client, request)
    [task] = [row for row in client.get("/api/my-work", headers=JIHO).json() if row["title"] == title]
    current = client.get(f"/api/tasks/{task['task_id']}", headers=JIHO).json()
    client.post(f"/api/tasks/{task['task_id']}/start", headers=JIHO, json={"expected_version": current["version"]})
    return task["task_id"]


def _report(client, task_id: str, summary: str, headers=JIHO):
    current = client.get(f"/api/tasks/{task_id}", headers=headers).json()
    return client.post(
        f"/api/tasks/{task_id}/completion-report",
        headers=headers,
        json={"expected_version": current["version"], "summary": summary},
    )


def _delivery_item(client, headers, task_id: str):
    return next(
        (row for row in client.get("/api/action-items", headers=headers).json()
         if row["kind"] == "task.delivery" and row["resource"]["id"] == task_id),
        None,
    )


def test_finishing_the_work_is_a_report_not_a_completion(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _requested_task(client)

    # The holder cannot close work someone else asked for; the command tells them what to do instead.
    current = client.get(f"/api/tasks/{task_id}", headers=JIHO).json()
    refused = client.post(f"/api/tasks/{task_id}/complete", headers=JIHO, json={"expected_version": current["version"]})
    assert refused.status_code == 422 and "완료 보고" in refused.text

    reported = _report(client, task_id, "초안과 최종본을 모두 정리했습니다")
    assert reported.status_code == 200, reported.text
    view = client.get(f"/api/tasks/{task_id}", headers=JIHO).json()
    # v2: 밖으로 나가는 상태는 넷뿐이다. 「제출했고 요청자가 아직 답하지 않았다」는 사실은
    # `state=done` + `derived.approval=awaiting_review` 로 말한다 (SPEC-003 §4 State).
    assert view["state"] == "done" and view["derived"]["approval"] == "awaiting_review"
    # Waiting for confirmation is not being done, on either side.
    # 목록도 같은 말을 한다: 밖으로는 `done` 이고, 아직 확인 전이라는 사실은 `derived.approval` 이 낸다.
    [row] = [row for row in client.get("/api/my-work", headers=JIHO).json() if row["task_id"] == task_id]
    assert row["state"] == "done" and row["derived"]["approval"] == "awaiting_review"


def test_the_person_who_asked_answers_a_question_of_their_own(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _requested_task(client, "분기 보고서")
    _report(client, task_id, "초안을 정리했습니다")

    # The acceptance question is finished; this is a new one, with its own identity and words.
    item = _delivery_item(client, MINA, task_id)
    assert item is not None, client.get("/api/action-items", headers=MINA).json()
    assert item["subject"] == "분기 보고서"
    assert item["operation_label"] == "업무 결과 확인"
    assert "결과" in item["current_question"]
    assert {command["id"] for command in item["allowed_commands"]} == {"accept", "request_changes"}
    assert item["waiting_on"]["member_id"] == "mina"

    # The holder is not the reviewer of work they did.
    holder_view = _delivery_item(client, JIHO, task_id)
    assert holder_view is None or holder_view["allowed_commands"] == []
    # And someone unrelated is not shown the question at all.
    assert _delivery_item(client, SORA, task_id) is None


def test_asking_for_more_keeps_the_same_question_and_adds_a_round(tmp_path) -> None:
    client, database_url = _stack(tmp_path)
    task_id = _requested_task(client, "보완이 필요한 업무")
    _report(client, task_id, "1차 결과입니다")
    item = _delivery_item(client, MINA, task_id)

    # A reason is required: "not enough" without saying what is not enough is not an answer.
    assert client.post(
        f"/api/action-items/{item['action_item_id']}/commands/request_changes",
        headers=MINA,
        json={"expected_version": item["expected_version"]},
    ).status_code == 422

    asked = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/request_changes",
        headers=MINA,
        json={"expected_version": item["expected_version"], "reason": "지난달 수치가 빠졌습니다"},
    )
    assert asked.status_code == 200, asked.text

    # The work goes on, and the holder can see why.
    view = client.get(f"/api/tasks/{task_id}", headers=JIHO).json()
    assert view["state"] == "in_progress"
    assert view["delivery"]["status"] == "awaiting_revision"
    assert view["delivery"]["last_reason"] == "지난달 수치가 빠졌습니다"

    # Reporting again is another round of the same question, not a second question.
    again = _report(client, task_id, "지난달 수치를 채웠습니다")
    assert again.status_code == 200, again.text
    reopened = _delivery_item(client, MINA, task_id)
    assert reopened["action_item_id"] == item["action_item_id"]
    assert reopened["submission_version"] == 2

    rounds = client.get(f"/api/action-items/{item['action_item_id']}", headers=MINA).json()["rounds"]
    assert [row["submission_version"] for row in rounds] == [1, 2]
    assert rounds[0]["snapshot"]["summary"] == "1차 결과입니다"
    assert rounds[1]["diff"]["summary"] == {"before": "1차 결과입니다", "after": "지난달 수치를 채웠습니다"}
    assert [decision["decision"] for decision in rounds[0]["decisions"]] == ["negotiate"]

    with make_session_factory(database_url)() as session:
        submissions = list(session.scalars(select(SubmissionRecord)))
        # Two rounds on the delivery question, and the acceptance question keeps its own single round.
        assert sum(1 for row in submissions if row.submission_version == 2) == 1


def test_accepting_the_result_closes_the_work_exactly_once(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _requested_task(client, "완료될 업무")
    _report(client, task_id, "결과입니다")
    item = _delivery_item(client, MINA, task_id)

    accepted = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/accept",
        headers=MINA,
        json={"expected_version": item["expected_version"]},
    )
    assert accepted.status_code == 200, accepted.text
    assert client.get(f"/api/tasks/{task_id}", headers=JIHO).json()["state"] == "done"

    # The same answer sent twice is one answer: a receipt, not a second closing.
    replay = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/accept",
        headers=MINA,
        json={"expected_version": item["expected_version"]},
    )
    assert replay.status_code == 200, replay.text
    assert client.get(f"/api/tasks/{task_id}", headers=JIHO).json()["version"] == client.get(
        f"/api/tasks/{task_id}", headers=MINA
    ).json()["version"]
    assert _delivery_item(client, MINA, task_id) is None  # it is answered; it is no longer waiting


@pytest.mark.parametrize("additional_binding", [False, True])
def test_a_report_freezes_what_was_delivered_and_what_the_task_then_was(tmp_path, additional_binding) -> None:
    client, database_url = _stack(tmp_path)
    task_id = _requested_task(client, "근거가 붙는 업무")
    client.post(f"/api/tasks/{task_id}/checklist", headers=JIHO, json={"text": "자료 모으기"})
    material = client.post(
        f"/api/tasks/{task_id}/materials/links",
        headers=JIHO,
        json={"kind": "output", "url": "https://docs.example.com/report", "label": "최종 보고서"},
    ).json()

    if additional_binding:
        from ax_workspace.platform.work_tasks import SqlAlchemyAttachmentRepository
        with make_session_factory(database_url)() as session:
            SqlAlchemyAttachmentRepository(session).bind(attachment_id=UUID(material["material_id"]), context_type="task", context_id=task_id, role="input", bound_by="jiho")
            session.commit()

    current = client.get(f"/api/tasks/{task_id}", headers=JIHO).json()
    reported = client.post(
        f"/api/tasks/{task_id}/completion-report",
        headers=JIHO,
        json={
            "expected_version": current["version"],
            "summary": "보고서를 올렸습니다",
            "output_material_ids": [material["material_id"]],
        },
    )
    assert reported.status_code == 200, reported.text

    [round_one] = client.get(
        f"/api/action-items/{_delivery_item(client, MINA, task_id)['action_item_id']}", headers=MINA
    ).json()["rounds"]
    snapshot = round_one["snapshot"]
    assert snapshot["summary"] == "보고서를 올렸습니다"
    assert snapshot["task_version"] == current["version"]
    assert [step["text"] for step in snapshot["checklist"]] == ["자료 모으기"]
    # Outputs are named by identity and integrity, never copied into the snapshot.
    assert [output["name"] for output in snapshot["outputs"]] == ["최종 보고서"]
    assert snapshot["outputs"][0]["integrity_ref"] == material["integrity_ref"]
    assert snapshot["outputs"][0]["material_id"] == material["material_id"]
    assert snapshot["outputs"][0]["binding_id"] == material["binding_id"]

    # Changing the task afterwards does not change what was reported.
    client.post(f"/api/tasks/{task_id}/checklist", headers=JIHO, json={"text": "나중에 추가한 단계"})
    [again] = client.get(
        f"/api/action-items/{_delivery_item(client, MINA, task_id)['action_item_id']}", headers=MINA
    ).json()["rounds"]
    assert [step["text"] for step in again["snapshot"]["checklist"]] == ["자료 모으기"]


def test_work_nobody_asked_for_still_completes_directly(tmp_path) -> None:
    """Only work someone asked for needs their confirmation. A Task of one's own does not grow a question."""
    client, _ = _stack(tmp_path)
    task_id = client.post("/api/tasks", headers=MINA, json={"title": "내 업무"}).json()["task_id"]
    current = client.get(f"/api/tasks/{task_id}", headers=MINA).json()
    client.post(f"/api/tasks/{task_id}/start", headers=MINA, json={"expected_version": current["version"]})
    started = client.get(f"/api/tasks/{task_id}", headers=MINA).json()

    completed = client.post(f"/api/tasks/{task_id}/complete", headers=MINA, json={"expected_version": started["version"]})
    assert completed.status_code == 200, completed.text
    assert client.get(f"/api/tasks/{task_id}", headers=MINA).json()["state"] == "done"
    assert [row for row in client.get("/api/action-items", headers=MINA).json() if row["kind"] == "task.delivery"] == []
    assert _report(client, task_id, "보고할 것 없음", MINA).status_code == 422
