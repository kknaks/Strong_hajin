"""PR #2 리뷰가 재현해 보인 것들 — 그 재현을 그대로 테스트로 옮긴다.

리뷰는 다섯 곳에서 「이렇게 하면 이렇게 된다」를 보여 주었다. 고치기 전에 그 재현이 실패로 남아야 고쳤다는 말을
할 수 있다. 각 테스트 이름 앞의 번호는 리뷰 코멘트 번호다.
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import (
    AccessGrantRecord,
    ActivityEventRecord,
    EmploymentPeriodRecord,
    MemberRecord,
    MembershipRecord,
    OrganizationUnitRecord,
    make_session_factory,
)

YUNA = {"X-Demo-Persona": "yuna"}   # scax 대표 — 조직 전체에 organization.manage
MINA = {"X-Demo-Persona": "mina"}   # 제품팀 구성원 — 관리 권한 없음
JIHO = {"X-Demo-Persona": "jiho"}   # 제품팀 팀장

FAKE_PHONE = "010-0000-0000"
FAKE_BIRTH = "1990-01-02"


def _client(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url))), database_url


def _give_contact(database_url, member_id: str) -> None:
    with make_session_factory(database_url)() as session:
        member = session.get(MemberRecord, member_id)
        member.phone = FAKE_PHONE
        member.birth_date = datetime.fromisoformat(FAKE_BIRTH).date()
        session.commit()


# ── F1 ───────────────────────────────────────────────────────────────────────
def test_f1_candidate_lists_never_carry_someone_s_contact(tmp_path) -> None:
    """후보 목록은 사람을 고르라고 있는 것이지 명부가 아니다 — 연락처가 따라 나가면 안 된다."""
    client, database_url = _client(tmp_path)
    _give_contact(database_url, "jiho")

    # 명부는 이미 가린다: 관리 권한이 없는 사람에게는 자리만 온다.
    directory = {row["id"]: row for row in client.get("/api/organization/members", headers=MINA).json()}
    assert directory["jiho"]["phone"] is None and directory["jiho"]["birth_date"] is None

    # 배정 후보는 task.assign을 가진 사람만 물을 수 있어 팀장으로 확인한다 — 개인정보 문제는 같다.
    for path, headers in (
        ("/api/work-request-cc-candidates", MINA),
        ("/api/work-request-assignee-candidates", MINA),
        ("/api/task-assignment-candidates", JIHO),
    ):
        answer = client.get(path, headers=headers)
        assert answer.status_code == 200, f"{path}: {answer.text}"
        rows = answer.json()
        assert FAKE_PHONE not in str(rows) and FAKE_BIRTH not in str(rows), path
        # 후보는 고를 수 있을 만큼만 말한다.
        assert all(set(row) == {"id", "display_name"} for row in rows), f"{path}: {rows}"

    # 참조자 후보는 실제로 사람을 돌려주고 있어야 이 테스트가 무언가를 지킨다.
    assert client.get("/api/work-request-cc-candidates", headers=MINA).json()


# ── F2 ───────────────────────────────────────────────────────────────────────
def test_f2_a_candidate_is_never_answered_as_having_no_account(tmp_path) -> None:
    """계정이 있는 사람을 「계정 없음」으로 말하지 않는다. 후보 응답은 계정을 말하지 않는다."""
    client, database_url = _client(tmp_path)
    for path, headers in (
        ("/api/work-request-assignee-candidates", MINA),
        ("/api/task-assignment-candidates", JIHO),
    ):
        rows = client.get(path, headers=headers).json()
        assert rows, path
        assert all("has_account" not in row for row in rows), path
        with make_session_factory(database_url)() as session:
            # 두 후보 조회는 계정 보유자만 고른다 — 응답이 그 반대를 말하고 있었다.
            assert all(session.get(MemberRecord, row["id"]).account_ref for row in rows), path

    # 명부는 여전히 계정 유무를 말한다.
    directory = client.get("/api/organization/members", headers=MINA).json()
    assert all("has_account" in row for row in directory)


# ── F8 ───────────────────────────────────────────────────────────────────────
def _second_company(database_url) -> None:
    """한 데이터베이스에 선 두 번째 회사. 이름도 사람도 이쪽 회사와 겹치지 않는다."""
    now = datetime.now(UTC)
    with make_session_factory(database_url)() as session:
        session.add(OrganizationUnitRecord(id="other-company", name="다른회사", unit_type_id="company", display_order=9))
        session.add(MemberRecord(id="other-one", display_name="다른회사 사람", employment_state="active", account_ref="local:x"))
        session.flush()
        session.add(EmploymentPeriodRecord(member_id="other-one", state="active", started_at=now))
        session.add(MembershipRecord(member_id="other-one", organization_id="other-company", is_primary=True, membership_kind="primary", valid_from=now))
        session.add(
            ActivityEventRecord(
                target_type="member",
                target_id="other-one",
                event_kind="access.grant_added",
                actor_id="other-one",
                reason="다른 회사의 사유",
                safe_summary="다른 회사에서 일어난 일",
                occurred_at=now,
            )
        )
        session.commit()


def test_f8_the_change_record_never_reaches_into_another_company(tmp_path) -> None:
    """조직을 말하지 않았다는 것은 「내 조직 전체」라는 뜻이지 「이 데이터베이스 전부」가 아니다."""
    client, database_url = _client(tmp_path)
    _second_company(database_url)
    granted = client.post(
        "/api/access/grants",
        headers=YUNA,
        json={"member_id": "mina", "role_id": "role:team-lead", "scope_ref": "product", "reason": "팀장 대행"},
    )
    assert granted.status_code == 201, granted.text

    # 다른 회사를 대놓고 물으면 거절한다 — 그 거절이 조건을 생략했을 때도 지켜져야 한다.
    assert client.get("/api/organization/activity?unit_id=other-company", headers=YUNA).status_code == 403

    everything = client.get("/api/organization/activity", headers=YUNA)
    assert everything.status_code == 200, everything.text
    events = everything.json()
    assert events, "자기 회사의 기록은 그대로 보여야 한다"
    assert "other-one" not in {event["target_id"] for event in events}
    assert "다른 회사의 사유" not in str(events) and "다른회사" not in str(events)


def test_f8_an_event_about_a_role_belongs_to_the_company_of_whoever_changed_it(tmp_path) -> None:
    """역할은 회사의 것이 아니라 제품의 것이다 — 그 사건은 바꾼 사람의 회사에 귀속한다."""
    client, database_url = _client(tmp_path)
    _second_company(database_url)
    now = datetime.now(UTC)
    with make_session_factory(database_url)() as session:
        session.add(
            ActivityEventRecord(
                target_type="role", target_id="role:team-lead", event_kind="access.role_changed",
                actor_id="yuna", reason="이쪽 회사가 바꿨다", safe_summary="역할 변경(이쪽)", occurred_at=now,
            )
        )
        session.add(
            ActivityEventRecord(
                target_type="role", target_id="role:team-lead", event_kind="access.role_changed",
                actor_id="other-one", reason="저쪽 회사가 바꿨다", safe_summary="역할 변경(저쪽)", occurred_at=now,
            )
        )
        session.commit()

    events = client.get("/api/organization/activity", headers=YUNA).json()
    summaries = {event["summary"] for event in events}
    assert "역할 변경(이쪽)" in summaries
    assert "역할 변경(저쪽)" not in summaries


# ── F4 ───────────────────────────────────────────────────────────────────────
def test_f4_events_that_happened_at_the_same_moment_are_all_reachable(tmp_path) -> None:
    """같은 시각에 일어난 일이 페이지 경계에 걸려 읽히지 않으면, 기록은 있으나 없는 것이 된다."""
    client, database_url = _client(tmp_path)
    moment = datetime.now(UTC)
    with make_session_factory(database_url)() as session:
        for index in range(3):
            session.add(
                ActivityEventRecord(
                    id=uuid4(), target_type="member", target_id="mina", event_kind="access.grant_added",
                    actor_id="yuna", reason=f"사유 {index}", safe_summary=f"같은 시각 {index}", occurred_at=moment,
                )
            )
        session.commit()

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):  # 넉넉히 돌려도 멈춰야 한다
        query = "/api/organization/activity?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(query, headers=YUNA).json()
        if not page:
            break
        seen.extend(event["summary"] for event in page)
        cursor = page[-1]["cursor"]

    assert sorted(summary for summary in seen if summary.startswith("같은 시각")) == [
        "같은 시각 0", "같은 시각 1", "같은 시각 2",
    ]
    assert len(seen) == len(set(seen)), "같은 사건이 두 번 나오면 안 된다"


# ── F5 ───────────────────────────────────────────────────────────────────────
def test_f5_a_grant_taken_back_early_ended_when_it_was_taken_back(tmp_path) -> None:
    """예정 만료일이 있어도, 먼저 끝난 쪽이 그 권한이 실제로 끝난 때다."""
    client, database_url = _client(tmp_path)
    granted = client.post(
        "/api/access/grants",
        headers=YUNA,
        json={"member_id": "mina", "role_id": "role:team-lead", "scope_ref": "product", "reason": "팀장 대행"},
    )
    grant_id = granted.json()["grant_id"]
    expires = datetime.now(UTC) + timedelta(days=30)
    with make_session_factory(database_url)() as session:
        session.get(AccessGrantRecord, UUID(grant_id)).valid_until = expires
        session.commit()

    assert client.post(f"/api/access/grants/{grant_id}/revoke", headers=YUNA, json={"reason": "대행 종료"}).status_code == 200

    history = client.get("/api/organization/members/mina/history?axis=grant", headers=YUNA).json()
    row = next(item for item in history if item.get("kind") == "unit" and item["value"] == "팀장")
    assert row["valid_until"] is not None
    ended = datetime.fromisoformat(row["valid_until"])
    # SQLite는 시간대를 붙이지 않고 돌려준다. 비교는 같은 기준에서 한다.
    ended = ended if ended.tzinfo else ended.replace(tzinfo=UTC)
    assert ended < expires, f"회수가 먼저인데 예정 만료일({expires.isoformat()})이 종료일로 나왔다: {row['valid_until']}"
