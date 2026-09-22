"""Corpus profiling reads only a fixed allowlist and never emits source text."""
import importlib.util
from pathlib import Path
import subprocess

import pytest


# 이 파일의 테스트는 **진짜 자식 프로세스**를 띄우고(자료·보고서 워커의 `IsolatedWork` spawn ·
# MCP `stdio_client` · `subprocess`) 그 진행을 초 단위 실시간 창으로 잰다 — 그래서 병렬 패스가 아니라
# `-n0` 직렬 패스에서 돈다. 기준과 걸개는 `tests/conftest.py`, 가르는 자리는 `Makefile` 의 `test-serial`.
pytestmark = pytest.mark.serial


def _module():
    path = Path(__file__).parents[2] / "scripts/material_corpus_profile.py"
    spec = importlib.util.spec_from_file_location("material_corpus_profile", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _root(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    return root


def test_manifest_excludes_paths_symlinks_and_content_secret_candidates(tmp_path):
    module = _module()
    root = _root(tmp_path)
    directory = root / "01_Notes/02_Permanent"
    directory.mkdir(parents=True)
    (directory / "검색.md").write_text("한국어 검색 품질 합성 문서", encoding="utf-8")
    (directory / "credential.md").write_text("not permitted", encoding="utf-8")
    (directory / "technical.md").write_text('password = "sensitive-value"', encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("not permitted", encoding="utf-8")
    (directory / "linked.md").symlink_to(outside)
    (directory / "private").mkdir()
    (directory / "private/hidden.md").write_text("not permitted", encoding="utf-8")
    manifest = module.build_manifest(root, per_class=4)
    assert [entry["path"] for entry in manifest["entries"]] == ["01_Notes/02_Permanent/검색.md"]
    assert "한국어" not in str(module.public_manifest_summary(manifest))
    for path in ("../outside.md", str(outside), "private/hidden.md", "01_Notes/02_Permanent/linked.md"):
        with pytest.raises(ValueError):
            module.allowed_path(root, path)


def test_manifest_hash_fences_source_changes_before_loading(tmp_path):
    module = _module()
    root = _root(tmp_path)
    directory = root / "02_PARA/03_Resources"
    directory.mkdir(parents=True)
    note = directory / "reference.md"
    note.write_text("검색용 합성 자료", encoding="utf-8")
    manifest = module.build_manifest(root, per_class=1)
    note.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="source changed"):
        module.read_manifest_sources(manifest)


def test_synthetic_profile_keeps_raw_text_out_of_metrics_and_removes_ephemeral_data(tmp_path):
    module = _module()
    root = _root(tmp_path)
    directory = root / "01_Notes/02_Permanent"
    directory.mkdir(parents=True)
    text = "\ufeff한국어 검색 품질 검증을 위한 고유색인문서입니다.\r\n줄바꿈도 보존합니다."
    (directory / "synthetic.md").write_text(text, encoding="utf-8")
    manifest = module.build_manifest(root, per_class=1)
    result = module.evaluate(manifest)
    assert result["correctness_pass"]
    assert result["provider_calls"] == 0 and result["temporary_data_removed"]
    assert result["source_hashes_unchanged"] and result["vault_status_unchanged"]
    assert text not in str(result) and "excerpt" not in str(result)
    assert result["queries"] and result["locator_checks"] > 0
    assert result["source_span_failures"] == 0


@pytest.mark.parametrize("prefix", ["ghp_", "github_pat_", "sk-proj-", "xoxb-"])
def test_bare_credential_prefix_is_excluded_without_a_surrounding_label(tmp_path, prefix):
    module = _module()
    root = _root(tmp_path)
    directory = root / "02_PARA/03_Resources"
    directory.mkdir(parents=True)
    (directory / "ordinary.md").write_text(prefix + "A" * 40, encoding="utf-8")
    manifest = module.build_manifest(root, per_class=1)
    assert manifest["entries"] == []
    assert manifest["excluded"]["content_candidate"] == 1
