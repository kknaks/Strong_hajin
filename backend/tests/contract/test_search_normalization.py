"""보이는 글자가 같으면 찾아진다 — 자모가 합쳐져 적혔든 풀려 적혔든.

한글은 컴퓨터 안에서 두 가지로 적힐 수 있다. macOS Finder가 주는 이름과 거기서 복사한 글자는 자모가 풀린
모양(NFD)이고, 키보드로 친 글자와 데이터베이스에 들어 있는 것은 합쳐진 모양(NFC)이다. 눈에는 구별되지 않으므로
사람은 자기가 무엇을 붙여넣었는지 알 수 없고, 아무것도 나오지 않는 이유도 알 수 없다.

전달받은 자료를 분류할 때 같은 문제를 이미 겪었다. 그때는 파일 이름만 맞췄고 검색은 그대로 두었다.
"""
import unicodedata

from fastapi.testclient import TestClient
import pytest

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.work.material_extraction import query_tokens, score_text

MINA = {"X-Demo-Persona": "mina"}
TITLE = "한빛의원 통합 마케팅"


def _decomposed(text: str) -> str:
    """Finder에서 복사해 붙여넣은 글자와 같은 모양."""
    return unicodedata.normalize("NFD", text)


@pytest.fixture()
def client(tmp_path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))


def test_searching_with_what_finder_gave_you_finds_the_work(client: TestClient) -> None:
    made = client.post("/api/tasks", headers=MINA, json={"title": TITLE})
    assert made.status_code == 201, made.text

    for query in ("한빛", TITLE):
        typed = client.get("/api/graph/search", headers=MINA, params={"q": query})
        pasted = client.get("/api/graph/search", headers=MINA, params={"q": _decomposed(query)})
        assert [node["id"] for node in typed.json()["nodes"]] == [made.json()["task_id"]], query
        assert typed.json()["nodes"] == pasted.json()["nodes"], f"붙여넣은 글자로는 찾지 못했습니다: {query}"


def test_a_pasted_query_still_becomes_search_words(client: TestClient) -> None:
    """자료 검색은 글자를 잘라 낱말로 만든다. 자모가 풀린 채로는 낱말이 하나도 나오지 않았다."""
    assert query_tokens(_decomposed("한빛 견적서")) == query_tokens("한빛 견적서")
    assert query_tokens(_decomposed("한빛 견적서")), "붙여넣은 질의가 낱말이 되지 않았습니다"
    # 본문이 어느 쪽으로 적혀 있든 같은 낱말로 세어진다.
    tokens = query_tokens("한빛")
    assert score_text(TITLE, tokens) == score_text(_decomposed(TITLE), tokens) == (1, 1)
