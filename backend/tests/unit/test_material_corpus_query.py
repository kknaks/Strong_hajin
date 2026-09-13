"""Corpus query token selection is a pure rule independent of Git and the application stack."""

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).parents[2] / "scripts/material_corpus_profile.py"
    spec = importlib.util.spec_from_file_location("material_corpus_profile_query", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_query_selection_does_not_cut_a_long_korean_run_into_artificial_fragments():
    module = _module()

    assert module.WORD.findall("초장문복합단어가계속이어지는합성문장") == []
    assert module.WORD.findall("검색어를 확인합니다") == ["검색어를", "확인합니다"]
