"""문서와 질문을 같은 규칙으로 자른다 — 그러지 않으면 찾을 수 있는 것과 없는 것이 이유 없이 갈린다.

한국어는 조사가 낱말에 붙어 있고, 같은 낱말도 어디에 있느냐에 따라 다르게 갈린다. 여기서 고정하는 것은 그
차이에도 문서와 질문이 서로 만난다는 것, 그리고 갈리면 안 되는 것은 갈리지 않는다는 것이다.
"""
from ax_workspace.platform.korean import analyzer


def _matches(document: str, question: str) -> bool:
    """색인된 문서에서 질문의 낱말이 하나라도 통째로 맞는가."""
    korean = analyzer()
    indexed = korean.index_text(document)
    return any(f" {token} " in indexed for token in korean.tokens(question))


def test_a_word_with_a_particle_still_meets_the_same_word_without_one() -> None:
    """`납기일은`과 `납기일`은 형태소로는 다르게 갈린다. 어절을 이어 붙인 형태가 둘을 만나게 한다."""
    korean = analyzer()
    assert korean.tokens("납기일은")[-1] == korean.tokens("납기일")[-1] == "납기일"
    assert _matches("공급사는 한빛상사이고 납기일은 2026-09-30입니다", "납기일")
    assert _matches("공급사는 한빛상사이고 납기일은 2026-09-30입니다", "한빛상사")


def test_a_product_code_is_never_split_into_something_else() -> None:
    """`AX-1200`이 `AX`와 `1200`이 되면 다른 코드와 섞인다. 원문 그대로의 형태가 함께 남는다."""
    assert "ax-1200" in analyzer().tokens("제품코드 AX-1200 확인")
    assert _matches("제품코드 AX-1200 확인", "AX-1200")


def test_one_syllable_does_not_swallow_a_longer_word() -> None:
    """`일`은 `일정`이 아니다. 낱말 통째로만 맞으므로 관계없는 자료가 답에 섞이지 않는다."""
    indexed = analyzer().index_text("회의록: 일정 조율")
    assert " 일정 " in indexed
    assert " 일 " not in indexed


def test_the_rules_carry_a_version_so_an_old_index_can_be_told_apart() -> None:
    """규칙이 바뀌면 그 규칙으로 만든 색인은 다시 만들어야 한다. 그 판단의 근거가 이 값이다."""
    version = analyzer().version
    assert version.startswith("kiwi-") and version.endswith("-r2")


def test_what_carries_no_meaning_is_left_out() -> None:
    """조사와 어미는 남기지 않는다. 남기면 모든 문서가 서로 비슷해진다."""
    found = analyzer().tokens("견적서를 검토했습니다")
    assert "견적서" in found
    assert "를" not in found and "습니다" not in found
