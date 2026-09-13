"""전달받은 자료는 열기 전에 분류된다.

The order matters more than the rules: a file that must never be read is refused by its path, so the decision itself
never opens it. Nothing is imported because it looked importable — a document waits for a person unless that person
has already put it on the allowlist.
"""

import os
import unicodedata

import pytest

from ax_workspace.modules.datasets.inventory import (
    Disposition,
    classify,
    is_denied,
    summarize,
)


def test_a_credential_file_is_refused_by_its_name_without_ever_being_opened(
    tmp_path,
) -> None:
    """이 test의 핵심은 규칙이 아니라 순서다: 열 수 없는 파일이어도 분류는 성공해야 한다."""
    secret = tmp_path / "★ 계정정보.xlsx"
    secret.write_text("이 내용은 절대 읽히면 안 됩니다", encoding="utf-8")
    os.chmod(secret, 0o000)
    try:
        entry = classify(str(secret), size_bytes=secret.stat().st_size)
        assert entry.disposition is Disposition.DENY
        # Proof that classification did not read it: opening it now still fails for this process.
        with pytest.raises(PermissionError):
            secret.read_bytes()
    finally:
        os.chmod(secret, 0o600)


@pytest.mark.parametrize(
    "name",
    [
        "★ 계정정보.xlsx",
        "계정정보 정리.docx",
        "2026 비밀번호 목록.txt",
        "server-credential.md",
        "backup.pem",
        ".env",
    ],
)
def test_names_that_say_credential_are_denied_wherever_they_sit(name: str) -> None:
    assert is_denied(f"/somewhere/deep/{name}") is True
    assert (
        classify(f"/somewhere/deep/{name}", size_bytes=10).disposition
        is Disposition.DENY
    )


def test_nothing_is_imported_until_a_person_says_so() -> None:
    ordinary = classify("/data/2026 월간 업무보고.xlsx", size_bytes=2048)
    assert ordinary.disposition is Disposition.MANUAL_REVIEW
    assert "허용 목록" in ordinary.reason

    allowed = classify(
        "/data/2026 월간 업무보고.xlsx", size_bytes=2048, allowlist=("*월간 업무보고*",)
    )
    assert allowed.disposition is Disposition.IMPORT

    # An allowlist never overrides a deny: the refusal is about what the file is, not about who listed it.
    assert (
        classify("/data/★ 계정정보.xlsx", size_bytes=2048, allowlist=("*",)).disposition
        is Disposition.DENY
    )


def test_a_format_the_product_cannot_read_is_metadata_at_most() -> None:
    assert (
        classify("/data/조직도.png", size_bytes=2048, allowlist=("*",)).disposition
        is Disposition.METADATA_ONLY
    )
    assert (
        classify("/data/원본.zip", size_bytes=2048).disposition
        is Disposition.METADATA_ONLY
    )
    # Size is its own decision, even for a readable format.
    huge = classify(
        "/data/전체 로그.csv", size_bytes=200 * 1024 * 1024, allowlist=("*",)
    )
    assert huge.disposition is Disposition.METADATA_ONLY and "큽니다" in huge.reason


def test_the_summary_counts_what_a_person_has_to_decide() -> None:
    entries = [
        classify("/d/a.docx", size_bytes=10),
        classify("/d/b.xlsx", size_bytes=20, allowlist=("b.xlsx",)),
        classify("/d/★ 계정정보.xlsx", size_bytes=30),
        classify("/d/c.png", size_bytes=40),
    ]
    counted = summarize(entries)
    assert counted["files"] == 4 and counted["bytes"] == 100
    assert counted["by_disposition"] == {
        "deny": 1,
        "import": 1,
        "manual-review": 1,
        "metadata-only": 1,
    }
    assert list(counted["by_suffix"])[0] == ".xlsx"


def test_a_name_the_filesystem_wrote_differently_is_still_the_same_name() -> None:
    """macOS는 한글 파일명을 분해해서(NFD) 저장한다. 조합형(NFC) 규칙과 그대로 비교하면 조용히 빗나간다.

    This is the shape of a real miss: the delivered folder's `★ 계정정보.xlsx` was stored decomposed, the deny rule was
    written composed, and the file was placed in `manual-review` instead of being refused.
    """
    decomposed = unicodedata.normalize("NFD", "★ 계정정보.xlsx")
    assert decomposed != "★ 계정정보.xlsx"
    assert is_denied(f"/전달자료/{decomposed}") is True
    assert (
        classify(f"/전달자료/{decomposed}", size_bytes=10).disposition
        is Disposition.DENY
    )
