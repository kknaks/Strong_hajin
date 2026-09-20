"""회의 승격이 **공통 생성 프레임의 값을 버리지 않는다** (WORK-003 gap B).

승격 창과 업무 화면의 창은 같은 폼이다. 그런데 `MeetingTodoPromotionInput` 은 다섯 칸뿐이라,
나머지 일곱은 `extra="forbid"` 로 422 가 되거나(엄격) 조용히 버려졌을 것이고(관대) — 고른 사람은
어느 쪽도 화면에서 볼 수 없다. 일곱이 요청 생성까지 **그대로 흐르는지**를 여기서 못 박는다.
"""
from uuid import UUID

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}


def test_promotion_carries_the_seven_shared_creation_fields_into_the_request(tmp_path) -> None:
    from test_meeting_finalize import _finalized, _stack, _the_final

    client, application, agent = _stack(tmp_path)
    meeting_id, _ = _finalized(client, application, agent)
    [todo] = _the_final(client, meeting_id, MINA)["todos"]

    project = client.post("/api/projects", headers=JIHO, json={"name": "가을 개편"}).json()["project_id"]
    for member_id in ("mina", "jiho", "yuna"):
        assert client.post(
            f"/api/projects/{project}/members", headers=JIHO, json={"member_id": member_id}
        ).status_code == 201
    parent = client.post(
        "/api/tasks", headers=JIHO, json={"title": "가을 개편 총괄", "project_id": project}
    ).json()
    predecessor = client.post(
        "/api/tasks", headers=MINA, json={"title": "규격 확정", "project_id": project}
    ).json()
    reference = client.post("/api/tasks", headers=MINA, json={"title": "지난 분기 정리"}).json()

    promoted = client.post(
        f"/api/meetings/{meeting_id}/todos/{todo['todo_id']}/promote",
        headers=MINA,
        json={
            "assignee_id": "jiho",
            # 공통 생성 프레임의 일곱 — 요청 생성이 이미 받는 값과 **같은 이름**이다.
            "start_date": "2026-10-01",
            "cc_member_ids": ["yuna"],
            "approver_id": "yuna",
            "reference_task_ids": [reference["task_id"]],
            "project_id": project,
            "parent_task_id": parent["task_id"],
            "preceding_task_ids": [predecessor["task_id"]],
        },
    )
    assert promoted.status_code == 201, promoted.text
    request_id = promoted.json()["linked"]["work_request_id"]

    request = client.get(f"/api/work-requests/{request_id}", headers=MINA).json()
    assert request["start_date"] == "2026-10-01"
    # 고른 참조자가 **버려지지 않았다**. 이 요청의 참조자 자리가 그밖에 누구를 더 세우는지는
    # 승격의 기존 계약이 답하는 것이고 이 시험이 다시 정하지 않는다.
    assert "yuna" in request["cc_member_ids"]
    assert request["approver_id"] == "yuna"
    assert request["project_id"] == project
    assert request["parent_task_id"] == parent["task_id"]
    assert request["preceding_task_ids"] == [predecessor["task_id"]]
    assert request["reference_task_ids"] == [reference["task_id"]]
    # 출처 두 열은 그대로다 — 일곱을 더하느라 승격이 일반 요청으로 새지 않았다.
    assert request["source_meeting_id"] == meeting_id
    assert UUID(request["request_id"]) == UUID(request_id)
