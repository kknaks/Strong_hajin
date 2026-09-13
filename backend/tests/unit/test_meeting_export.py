import re

from ax_workspace.modules.meetings.export import render_meeting_html


def _document(*, purpose: str = "결정할 것", todos: list[dict] | None = None) -> dict:
    return {
        "meeting": {
            "title": "권한 <모델>",
            "purpose": purpose,
            "starts_at": "2026-09-05T02:00:00+00:00",
            "ends_at": "2026-09-05T03:00:00+00:00",
            "started_at": "2026-09-05T02:00:00+00:00",
            "attendees": [{"member_id": "mina", "display_name": "민아"}],
            "external_attendees": ["김외부"],
        },
        "agendas": [
            {
                "agenda_id": "secret-agenda-id",
                "order": 1,
                "title": "첫 안건",
                "concluded": True,
                "lines": [
                    {
                        "line_id": "line-1",
                        "text": "<script>alert(1)</script> & 결론",
                        "at_ms": None,
                        "evidence": [{"from_ms": 0, "to_ms": 400}],
                    }
                ],
                "todos": todos if todos is not None else [],
            }
        ],
    }


def test_export_is_a_self_contained_a4_document() -> None:
    body = render_meeting_html(_document())

    assert re.search(r"<link\b|<script\b|@import|url\(", body) is None
    assert "-apple-system" in body and "system-ui" in body
    assert "@page { size: A4; margin: 0; }" in body
    assert "width: 210mm;" in body and "min-height: 297mm;" in body
    assert "break-inside: avoid" in body and "page-break-inside: avoid" in body
    assert '<span class="page-no">1</span>' in body


def test_export_draws_thread_chips_times_and_only_nonempty_follow_ups() -> None:
    body = render_meeting_html(
        _document(
            todos=[
                {
                    "title": "근거를 딛은 일",
                    "due_candidate": None,
                    "reference": {"line_ids": ["line-1"]},
                    "linked": None,
                }
            ]
        )
    )

    assert '<span class="chip done">결론 남</span>' in body
    assert '<span class="chip action">액션</span>' in body and "다음 할 일" in body
    assert '<span class="stamp">11:00</span>' in body
    assert '<span class="at">11:00</span>' in body
    assert "참석 · 민아, 김외부" in body
    assert '<div class="todos">' not in render_meeting_html(_document(todos=[]))


def test_export_escapes_people_text_and_omits_internal_ids() -> None:
    body = render_meeting_html(_document())

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; 결론" in body
    assert "secret-agenda-id" not in body and "line-1" not in body


def test_export_paginates_long_notes_and_omits_an_empty_summary() -> None:
    document = _document(purpose="")
    document["agendas"] = [
        {
            "order": index,
            "title": f"안건 {index}",
            "concluded": False,
            "lines": [
                {
                    "line_id": f"l{index}-{row}",
                    "text": "가" * 55,
                    "at_ms": row * 1000,
                    "evidence": [],
                }
                for row in range(12)
            ],
            "todos": [],
        }
        for index in range(1, 9)
    ]

    body = render_meeting_html(document)

    assert body.count('<section class="page"') >= 2
    assert '<span class="page-no">2</span>' in body
    assert body.count('class="sheet-head cont"') == body.count('<section class="page"') - 1
    assert body.count("참석 · ") == 1
    assert 'class="sheet-summary"' not in body


def test_follow_up_marks_missing_evidence_requested_state_and_due_date() -> None:
    body = render_meeting_html(
        _document(
            todos=[
                {
                    "title": "근거 없는 일",
                    "due_candidate": None,
                    "reference": {"line_ids": []},
                    "linked": None,
                },
                {
                    "title": "이미 보낸 일",
                    "due_candidate": "2026-09-10",
                    "reference": {"line_ids": []},
                    "linked": {"work_request_id": "request-1", "task_id": None},
                },
            ]
        )
    )

    assert '<span class="stamp none">근거 없음</span>' in body
    assert '<span class="chip requested">요청됨</span>' in body
    assert '<span class="due">09-10</span>' in body
