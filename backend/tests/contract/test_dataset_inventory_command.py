"""The dataset inventory command keeps delivered data outside the repository and reports only metadata."""

import json
from pathlib import Path
import unicodedata

from ax_workspace.entrypoints.dataset import main


def test_inspect_writes_only_safe_metadata_for_a_delivered_folder(tmp_path) -> None:
    body = "이_문서의_내용은_보고서에_절대_나오면_안_된다"
    delivered = tmp_path / "thesc"
    report_directory = delivered / "보고"
    report_directory.mkdir(parents=True)
    (report_directory / "2026 월간 업무보고.xlsx").write_text(body, encoding="utf-8")
    (delivered / "회의록.docx").write_text(body, encoding="utf-8")
    (delivered / "★ 계정정보.xlsx").write_text(body, encoding="utf-8")
    (delivered / "조직도.png").write_text(body, encoding="utf-8")
    (delivered / ".DS_Store").write_text(body, encoding="utf-8")

    decomposed_directory = delivered / unicodedata.normalize(
        "NFD", "프로젝트별 공유폴더"
    )
    decomposed_directory.mkdir()
    decomposed_name = unicodedata.normalize("NFD", "★ 계정정보.xlsx")
    (decomposed_directory / decomposed_name).write_text(body, encoding="utf-8")

    out = tmp_path / "inventory.json"
    assert (
        main(
            ["inspect", str(delivered), "--allow", "*월간 업무보고*", "--out", str(out)]
        )
        == 0
    )

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["summary"]["files"] == 5  # .DS_Store is noise, not a decision.
    assert report["summary"]["by_disposition"] == {
        "deny": 2,
        "import": 1,
        "manual-review": 1,
        "metadata-only": 1,
    }
    placed = {
        unicodedata.normalize("NFC", row["path"]): row["disposition"]
        for row in report["files"]
    }
    assert placed["보고/2026 월간 업무보고.xlsx"] == "import"
    assert placed["★ 계정정보.xlsx"] == "deny"
    assert placed["회의록.docx"] == "manual-review"
    assert placed["프로젝트별 공유폴더/★ 계정정보.xlsx"] == "deny"
    assert body not in out.read_text(encoding="utf-8")


def test_inspect_refuses_repository_inputs_and_outputs(tmp_path, capsys) -> None:
    repository = Path(__file__).resolve().parents[3]
    assert main(["inspect", str(repository / "backend")]) == 2
    assert "저장소 밖" in capsys.readouterr().err

    delivered = tmp_path / "thesc"
    delivered.mkdir()
    (delivered / "a.docx").write_text("x", encoding="utf-8")
    assert (
        main(["inspect", str(delivered), "--out", str(repository / "leaked.json")]) == 2
    )
    assert not (repository / "leaked.json").exists()
