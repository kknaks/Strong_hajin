from datetime import UTC, date, datetime
import json

import pytest

from ax_workspace.modules.meetings.batch import (
    BATCH_CHARS,
    BATCH_MAX_WAIT_SECONDS,
    BATCH_SWITCH_MIN_CHARS,
    BatchTriggerContext,
    BatchTriggerDecision,
    BatchLine,
    CAUSE_AGENDA_SWITCH,
    CAUSE_TIMER,
    CAUSE_TRANSCRIPT,
    SchemaViolation,
    build_batch_prompt,
    build_warm_start_prompt,
    demote_line,
    parse_output,
    decide_batch_trigger,
)
from ax_workspace.modules.meetings.domain import (
    AGENDA_SOURCES,
    LINE_TRACKS,
    TRACK_AI,
    TRACK_FINAL,
    TRACK_MEMO,
    MeetingError,
    MeetingStateConflict,
    MeetingStatus,
    ensure_agenda_source,
    ensure_line_track,
    ensure_transition,
    surviving_lineage,
    normalize_agenda_order,
    normalize_external_attendees,
    normalize_optional_text,
    validate_meeting_schedule,
)
from ax_workspace.modules.meetings.finalize import (
    FinalizationContext,
    FINAL_OUTPUT_SCHEMA,
    FinalAgenda,
    FinalNotes,
    FinalTodo,
    build_final_prompt,
    finalize_notes,
    floor_due,
    is_already_work,
    parse_final_output,
    resolve_due,
)
from ax_workspace.modules.meetings.material_policy import accepts


def test_meeting_lifecycle_allows_only_the_transitions_in_the_domain_map() -> None:
    allowed = (
        (MeetingStatus.SCHEDULED, MeetingStatus.IN_PROGRESS),
        (MeetingStatus.SCHEDULED, MeetingStatus.CANCELLED),
        (MeetingStatus.IN_PROGRESS, MeetingStatus.SUMMARIZING),
        (MeetingStatus.SUMMARIZING, MeetingStatus.DONE),
        (MeetingStatus.SUMMARIZING, MeetingStatus.FAILED),
        (MeetingStatus.FAILED, MeetingStatus.SUMMARIZING),
        (MeetingStatus.CANCELLED, MeetingStatus.SCHEDULED),
    )
    refused = (
        (MeetingStatus.DONE, MeetingStatus.SUMMARIZING),
        (MeetingStatus.SCHEDULED, MeetingStatus.DONE),
        (MeetingStatus.SCHEDULED, MeetingStatus.SUMMARIZING),
        (MeetingStatus.IN_PROGRESS, MeetingStatus.CANCELLED),
        (MeetingStatus.CANCELLED, MeetingStatus.IN_PROGRESS),
        (MeetingStatus.DONE, MeetingStatus.IN_PROGRESS),
    )

    for source, target in allowed:
        assert ensure_transition(source, target) is target
    for source, target in refused:
        with pytest.raises(MeetingStateConflict):
            ensure_transition(source, target)


def test_agenda_source_vocabulary_is_closed_and_belongs_to_the_memo_track_alone() -> None:
    """**출처는 사람 벌 안의 출처다** (SPEC-004 v0.5 §4.1-2 · D51).

    0.4.x 의 「AI 정리」(`ai`)는 은퇴했다 — 그 값이 있던 이유는 AI 가 사람과 **같은 목록**에 안건을
    세웠기 때문이고, 벌이 갈렸으므로 「AI 가 세웠다」는 출처가 아니라 **벌 자체**가 말한다.
    """
    assert AGENDA_SOURCES == {"manual", "set", "carried", "derived"}
    assert "ai" not in AGENDA_SOURCES
    for source in AGENDA_SOURCES:
        assert ensure_agenda_source(source, track=TRACK_MEMO) == source
    for unknown in ("imported", "AI", "", "manual ", "ai"):
        with pytest.raises(MeetingError):
            ensure_agenda_source(unknown, track=TRACK_MEMO)

    # 다른 두 벌은 출처를 갖지 않는다 — `None` 만 통과하고 값이 오면 거절한다.
    for track in (TRACK_AI, TRACK_FINAL):
        assert ensure_agenda_source(None, track=track) is None
        with pytest.raises(MeetingError, match="memo"):
            ensure_agenda_source("manual", track=track)


def test_a_line_hangs_only_on_an_agenda_of_its_own_track() -> None:
    """**줄의 벌과 그 줄이 매달린 안건의 벌은 언제나 같다** (SPEC §4.2-9 · §4.0-1).

    다른 벌의 안건 id 를 실은 줄은 거절한다 — 이 한 줄이 「벌이 갈렸다」를 지킨다.
    """
    for track in LINE_TRACKS:
        assert ensure_line_track(track, agenda_track=track) == track
    with pytest.raises(MeetingError, match="does not hang"):
        ensure_line_track(TRACK_AI, agenda_track=TRACK_MEMO)
    with pytest.raises(MeetingError, match="does not hang"):
        ensure_line_track(TRACK_MEMO, agenda_track=TRACK_AI)
    with pytest.raises(MeetingError, match="does not hang"):
        ensure_line_track(TRACK_FINAL, agenda_track=TRACK_MEMO)
    with pytest.raises(MeetingError, match="must be one of"):
        ensure_line_track("origin", agenda_track="origin")


def test_lineage_keeps_only_ids_that_point_at_this_meeting_and_drops_the_rest() -> None:
    """계보는 **존재만 검증한다** — 없는 id 는 그 id 만 버리고 안건·줄 자체는 산다 (SPEC §8-6 · §4.2-10).

    맞는지는 검증하지 않는다: AI 의 자기보고라 서버가 확인할 방법이 없다.
    """
    known = {"a", "b"}
    assert surviving_lineage(["a", "없는것", "b", "a"], known_ids=known) == ["a", "b"]
    assert surviving_lineage([], known_ids=known) == []
    assert surviving_lineage(None, known_ids=known) == []
    assert surviving_lineage(["없는것"], known_ids=known) == []


def test_meeting_command_values_are_normalized_before_the_repository_sees_them() -> None:
    assert normalize_optional_text("  회의 제목  ", label="meeting title", limit=20) == "회의 제목"
    assert normalize_optional_text("  ", label="meeting title", limit=20) is None
    assert normalize_external_attendees([" 김 외부 ", "", "김 외부", None, "박 외부"]) == (
        "김 외부",
        "박 외부",
    )
    assert normalize_agenda_order("2") == 2


def test_meeting_command_value_boundaries_fail_in_the_domain() -> None:
    with pytest.raises(MeetingError, match="at most 3"):
        normalize_optional_text("1234", label="meeting title", limit=3)
    with pytest.raises(MeetingError, match="at most 100"):
        normalize_external_attendees(["가" * 101])
    for value in (0, "not-a-number"):
        with pytest.raises(MeetingError):
            normalize_agenda_order(value)


def test_meeting_schedule_requires_aware_increasing_datetimes() -> None:
    start = datetime(2026, 9, 13, 3, 0, tzinfo=UTC)
    validate_meeting_schedule(start, start.replace(hour=4))

    with pytest.raises(MeetingError, match="timezone"):
        validate_meeting_schedule(start.replace(tzinfo=None), start.replace(hour=4))
    with pytest.raises(MeetingError, match="before end"):
        validate_meeting_schedule(start, start)


def test_follow_up_due_date_uses_spoken_date_then_next_meeting_then_nothing() -> None:
    spoken = FinalTodo("a", "d", date(2026, 9, 20), ["x", "y"], [])
    empty = FinalTodo("a", "d", None, ["x", "y"], [])

    assert resolve_due(spoken, next_meeting_starts_on=date(2026, 9, 30)) == date(2026, 9, 20)
    assert resolve_due(empty, next_meeting_starts_on=date(2026, 9, 30)) == date(2026, 9, 29)
    assert resolve_due(empty, next_meeting_starts_on=None) is None


def test_a_due_before_the_meeting_day_is_cleared_and_the_same_day_is_kept() -> None:
    """SPEC-010 §4.8 — ①이든 ②든 회의일(KST)보다 이르면 비운다. 같은 날은 허용."""
    meeting_day = date(2026, 10, 7)
    early = FinalTodo("a", "d", date(2026, 10, 6), ["x", "y"], [])
    same = FinalTodo("a", "d", date(2026, 10, 7), ["x", "y"], [])
    empty = FinalTodo("a", "d", None, ["x", "y"], [])

    assert resolve_due(early, next_meeting_starts_on=None, meeting_starts_on=meeting_day) is None
    assert resolve_due(same, next_meeting_starts_on=None, meeting_starts_on=meeting_day) == meeting_day
    # ② 다음 회의가 같은 날이면 그 전날은 회의일보다 이르다 → 비운다 · 다음 날이면 전날 = 회의일 → 허용.
    assert resolve_due(empty, next_meeting_starts_on=date(2026, 10, 7), meeting_starts_on=meeting_day) is None
    assert resolve_due(empty, next_meeting_starts_on=date(2026, 10, 8), meeting_starts_on=meeting_day) == meeting_day
    assert floor_due(None, meeting_starts_on=meeting_day) is None
    assert floor_due(date(2026, 10, 1), meeting_starts_on=None) == date(2026, 10, 1)


def test_existing_work_match_is_conservative_and_title_normalized() -> None:
    assert is_already_work("계약서 검토", {"계약서검토"}) is True
    assert is_already_work("계약서를 검토한다", {"계약서검토"}) is False
    assert is_already_work("전혀 다른 일", {"계약서검토"}) is False


def test_final_note_preparation_owns_evidence_titles_sources_due_dates_and_duplicates() -> None:
    notes = FinalNotes(
        title_candidate="AI 제목",
        agendas=[
            FinalAgenda(
                title="결론",
                merged_from=[],
                concluded=True,
                lines=[
                    BatchLine(
                        text="근거가 있는 결론",
                        evidence=[
                            {"from_ms": 0, "to_ms": 900},
                            {"from_ms": 90_000, "to_ms": 91_000},
                        ],
                        task_id=None,
                    )
                ],
                todos=[
                    FinalTodo("계약서 검토", "이미 있는 일", None, ["확인", "회신"], []),
                    FinalTodo("새 일", "새로 할 일", None, ["확인", "회신"], []),
                    FinalTodo("새 일", "같은 출력의 중복", None, ["확인", "회신"], []),
                ],
            )
        ],
    )

    outcome = finalize_notes(
        notes,
        FinalizationContext(
            covered_ms=(0, 10_000),
            meeting_title="사람이 붙인 제목",
            existing_task_titles=frozenset({"계약서검토"}),
            next_meeting_starts_on=date(2026, 9, 30),
        ),
    )
    prepared = outcome.notes

    assert prepared is not notes
    assert notes.title_candidate == "AI 제목"
    assert prepared.title_candidate is None
    assert prepared.agendas[0].lines[0].evidence == [{"from_ms": 0, "to_ms": 900}]
    assert [todo.title for todo in prepared.agendas[0].todos] == ["새 일"]
    [todo] = prepared.agendas[0].todos
    assert todo.due_candidate == date(2026, 9, 29)
    assert todo.description.endswith("회의 사람이 붙인 제목 · 안건 1 에서")


def test_titleless_final_note_uses_the_candidate_in_its_source_stamp() -> None:
    notes = FinalNotes(
        title_candidate="후보 제목",
        agendas=[
            FinalAgenda(
                title="결론",
                merged_from=[],
                concluded=False,
                todos=[FinalTodo("후속", "설명", None, ["확인", "회신"], [])],
            )
        ],
    )

    outcome = finalize_notes(
        notes,
        FinalizationContext(
            covered_ms=(0, 0),
            meeting_title=None,
            existing_task_titles=frozenset(),
            next_meeting_starts_on=None,
        ),
    )

    assert notes.title_candidate == "후보 제목"
    assert not notes.agendas[0].todos[0].description.endswith("회의 후보 제목 · 안건 1 에서")
    assert outcome.notes.agendas[0].todos[0].description.endswith("회의 후보 제목 · 안건 1 에서")


def test_final_output_schema_owns_follow_up_shape_and_rejects_partial_answers() -> None:
    todo_schema = FINAL_OUTPUT_SCHEMA["properties"]["agendas"]["items"]["properties"]["todos"]["items"]
    assert set(todo_schema["required"]) == {
        "title", "description", "due_candidate", "checklist_candidate", "line_ids"
    }
    assert "assignee" not in json.dumps(FINAL_OUTPUT_SCHEMA)

    with pytest.raises(SchemaViolation):
        parse_final_output("준비됨")
    broken = {
        "title_candidate": None,
        "agendas": [{
            "agenda_id": None,
            "title": "a",
            "source": "ai",
            "concluded": False,
            "lines": [],
            "todos": [{
                "title": "t",
                "description": "d",
                "due_candidate": None,
                "checklist_candidate": ["하나뿐"],
                "line_ids": [],
            }],
        }],
    }
    with pytest.raises(SchemaViolation):
        parse_final_output(json.dumps(broken))


def test_meeting_material_admission_uses_only_pdf_and_markdown() -> None:
    assert accepts("정리.md", "text/plain") is True
    assert accepts("정리.markdown", "") is True
    assert accepts("계약서.pdf", "application/pdf") is True
    assert accepts("사진.png", "image/png") is False
    assert accepts("압축.zip", "application/zip") is False


def test_batch_output_rejects_non_json_and_unknown_sources() -> None:
    with pytest.raises(SchemaViolation):
        parse_output("준비됨")
    invalid = {
        "agendas": [{"agenda_id": None, "title": "", "source": "unknown", "lines": [], "todos": []}]
    }
    with pytest.raises(SchemaViolation):
        parse_output(json.dumps(invalid))


def test_batch_trigger_policy_owns_volume_switch_and_timer_thresholds() -> None:
    assert (BATCH_CHARS, BATCH_SWITCH_MIN_CHARS, BATCH_MAX_WAIT_SECONDS) == (600, 80, 90)
    cases = (
        (CAUSE_TRANSCRIPT, 599, False, True),
        (CAUSE_TRANSCRIPT, 600, True, False),
        (CAUSE_AGENDA_SWITCH, 79, False, True),
        (CAUSE_AGENDA_SWITCH, 80, True, False),
        (CAUSE_TIMER, 0, False, False),
        (CAUSE_TIMER, 1, True, False),
    )
    for cause, pending, fire, arm_timer in cases:
        assert decide_batch_trigger(BatchTriggerContext(cause=cause, pending_chars=pending)) == (
            BatchTriggerDecision(fire=fire, arm_timer=arm_timer)
        )

    with pytest.raises(ValueError, match="알 수 없는 트리거"):
        decide_batch_trigger(BatchTriggerContext(cause="unknown", pending_chars=1))


def test_batch_line_validation_drops_only_untrusted_references() -> None:
    line = BatchLine(
        text="본문은 산다",
        evidence=[
            {"from_ms": 0, "to_ms": 500},
            {"from_ms": 90_000, "to_ms": 91_000},
        ],
        task_id="missing",
    )

    assert demote_line(line, allowed_task_ids=set(), covered_ms=(0, 1_000)) == BatchLine(
        text="본문은 산다",
        evidence=[{"from_ms": 0, "to_ms": 500}],
        task_id=None,
    )


def test_batch_prompts_carry_the_json_contract() -> None:
    prompt = build_batch_prompt([{"speakerLabel": "1", "atMs": 0, "endMs": 900, "text": "말"}], [])
    assert "JSON 하나로만 답하라" in prompt
    assert '"agendas"' in prompt and '"additionalProperties": false' in prompt

    warm = build_warm_start_prompt({"title": "회의"}, ("task_list",))
    assert "JSON 스키마 하나로만" in warm


def test_final_prompt_carries_the_meeting_day_for_relative_due_dates() -> None:
    prompt = build_final_prompt(
        meeting={"title": "회의", "starts_on": "2026-09-13 (일)"},
        memo_agendas=[],
        ai_agendas=[],
        memo_lines=[],
        ai_lines=[],
        transcript=[],
    )

    assert "기준일: 2026-09-13 (일)" in prompt
    assert "YYYY-MM-DD" in prompt and "환산" in prompt


# ── WORK-012 WP2-BE — 용어 보정 표 · 안건 순서 · 배치에 온 보정 표 (SPEC-010 §4.7) ─────────────────────────────────


def _final_body(**extra) -> str:
    import json as _json

    return _json.dumps(
        {
            "title_candidate": None,
            "agendas": [
                {"title": "늦은 안건", "merged_from": [], "concluded": False, "todos": [],
                 "lines": [{"text": "뒤", "evidence": [{"from_ms": 50_000, "to_ms": 51_000}], "from_lines": []}]},
                {"title": "줄 없는 안건", "merged_from": [], "concluded": False, "lines": [], "todos": []},
                {"title": "이른 안건", "merged_from": [], "concluded": False, "todos": [],
                 "lines": [{"text": "앞", "evidence": [{"from_ms": 1_000, "to_ms": 2_000}], "from_lines": []},
                           {"text": "더 뒤", "evidence": [{"from_ms": 70_000, "to_ms": 71_000}], "from_lines": []}]},
            ],
            **extra,
        },
        ensure_ascii=False,
    )


def test_term_corrections_keep_valid_rows_in_order_and_drop_only_the_bad_ones() -> None:
    """형식 오류 항목만 버린다 — 시도를 실패시키지 않는다 (SPEC-010 §4.7-6 · OQ-1013)."""
    notes = parse_final_output(_final_body(term_corrections=[
        {"heard": "캐스티", "corrected": "CASTI", "grade": "auto"},
        {"heard": "차티", "corrected": "김차태", "grade": "presumed"},
        {"heard": "캐스티", "corrected": "다른 것", "grade": "auto"},          # 같은 들린 말 — 첫 것만
        {"heard": "", "corrected": "빈 들린 말", "grade": "auto"},              # 길이 0
        {"heard": "가" * 101, "corrected": "길다", "grade": "auto"},           # 100자 초과
        {"heard": "등급", "corrected": "틀림", "grade": "maybe"},              # 등급 밖
        {"heard": "칸", "corrected": None, "grade": "auto"},                    # 타입
        "문자열 항목",
    ]))
    assert [(row.heard, row.corrected, row.grade) for row in notes.term_corrections] == [
        ("캐스티", "CASTI", "auto"),
        ("차티", "김차태", "presumed"),
    ]


def test_a_missing_or_malformed_term_table_is_none_not_an_empty_table_nor_a_failed_attempt() -> None:
    """칸이 없거나 배열이 아니면 `None` — 정정 pass 를 건너뛴 응답이다. `[]`(돌았는데 없음)와 가른다 (WP2 수정 1 W-2 · H-4).
    시도는 실패시키지 않는다(본문은 섰다)."""
    assert parse_final_output(_final_body()).term_corrections is None
    assert parse_final_output(_final_body(term_corrections="표가 아님")).term_corrections is None
    assert parse_final_output(_final_body(term_corrections=[])).term_corrections == []


def test_the_provider_schema_requires_the_term_table() -> None:
    assert "term_corrections" in FINAL_OUTPUT_SCHEMA["required"]
    assert FINAL_OUTPUT_SCHEMA["properties"]["term_corrections"]["items"]["properties"]["grade"]["enum"] == ["auto", "presumed"]


def test_final_agendas_are_ordered_by_their_earliest_evidence_and_lineless_ones_go_last() -> None:
    """D-15 — 모델 순서가 아니라 회의 흐름 순서(가장 이른 근거 시작). 줄 없는 안건은 끝."""
    notes = parse_final_output(_final_body())
    outcome = finalize_notes(
        notes,
        FinalizationContext(covered_ms=(0, 100_000), meeting_title="회의", existing_task_titles=frozenset(), next_meeting_starts_on=None),
    )
    assert [agenda.title for agenda in outcome.notes.agendas] == ["이른 안건", "늦은 안건", "줄 없는 안건"]


def test_a_batch_output_with_a_term_table_drops_the_table_not_the_batch() -> None:
    """중간 배치는 정정하지 않는다 (D-16) — 보정 표가 오면 그 칸만 버리고 본문은 산다."""
    import json as _json

    from ax_workspace.modules.meetings.batch import parse_output

    body = _json.dumps({
        "agendas": [{"title": "화제", "lines": [], "todos": []}],
        "term_corrections": [{"heard": "캐스티", "corrected": "CASTI", "grade": "auto"}],
    }, ensure_ascii=False)
    [agenda] = parse_output(body)
    assert agenda.title == "화제"
