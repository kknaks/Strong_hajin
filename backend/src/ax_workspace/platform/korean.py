"""한국어 본문과 질문을 같은 규칙으로 잘라 낱말로 만든다.

한국어는 조사가 낱말에 붙어 있어서 `견적서를`과 `견적서`가 글자로는 다르다. 문서와 질문에 같은 분석을 적용해야
같은 것을 같다고 볼 수 있고, 한쪽만 분석하면 찾을 수 있는 것과 없는 것이 이유 없이 갈린다.

분석이 늘 옳지는 않다 — `납기일`이 `납`과 `기일`로 갈리기도 한다. 그래도 문서와 질문에 똑같이 갈리므로 서로
만나며, 그렇게 갈리지 않아야 하는 사내 용어는 사용자 사전이 답한다.

제품 코드와 문서 번호는 갈리면 안 된다. `AX-1200`이 `AX`와 `1200`이 되면 다른 코드와 섞이므로, 분석한 낱말과
함께 원문 그대로의 형태도 남긴다. 정확히 그것을 찾는 사람이 정확히 그것을 찾는다.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

#: 뜻을 지고 있는 품사만 남긴다 — 이름, 움직임, 상태, 외국어, 숫자, 그리고 통째로 하나인 연속열.
_CONTENT_TAGS = ("NN", "VV", "VA", "XR", "SL", "SH", "SN", "W_")
#: 원문 그대로 남길 것: 코드·번호처럼 갈리면 다른 것이 되는 연속열.
_VERBATIM = re.compile(r"[0-9A-Za-z][0-9A-Za-z._\-/]{1,}")
#: 분석 규칙이 바뀌면 이 숫자가 바뀌고, 그 규칙으로 만든 색인은 다시 만들어야 한다.
RULES_VERSION = 2


class KoreanAnalyzer:
    """Kiwi로 자르고, 갈리면 안 되는 것은 원형으로 함께 남긴다."""

    def __init__(self) -> None:
        self._kiwi = None

    @property
    def version(self) -> str:
        import kiwipiepy

        return f"kiwi-{kiwipiepy.__version__}-r{RULES_VERSION}"

    def _analyzer(self):
        if self._kiwi is None:
            # 모델을 여는 데 1초쯤 걸린다. 처음 쓸 때 한 번만 연다.
            from kiwipiepy import Kiwi

            self._kiwi = Kiwi()
        return self._kiwi

    def tokens(self, text: str) -> list[str]:
        """문서에도 질문에도 같은 이것을 쓴다. 순서를 지키고 중복은 지운다.

        같은 낱말이 어디에 있느냐에 따라 다르게 갈리는 일이 있다 — `납기일은`은 `납·기일`로, 홀로 선 `납기일`은
        `납기·일`로 갈린다. 그러면 문서와 질문이 서로 만나지 못한다. 그래서 낱낱의 형태소와 함께, 한 어절에서
        조사를 뗀 뒤 이어 붙인 형태도 남긴다. 그 형태는 어느 쪽이든 `납기일`로 같다.
        """
        cleaned = unicodedata.normalize("NFC", str(text or ""))
        if not cleaned.strip():
            return []
        found: list[str] = []
        for eojeol in cleaned.split():
            joined: list[str] = []
            for token in self._analyzer().tokenize(eojeol):
                if not token.tag.startswith(_CONTENT_TAGS):
                    continue
                found.append(token.form.lower())
                joined.append(token.form.lower())
            if len(joined) > 1:
                found.append("".join(joined))
        # 갈리면 다른 것이 되는 연속열은 원문 그대로도 남긴다.
        found.extend(match.group(0).lower() for match in _VERBATIM.finditer(cleaned))
        return list(dict.fromkeys(found))

    def index_text(self, text: str) -> str:
        """데이터베이스가 색인할 형태 — 낱말을 공백으로 이어 붙이고 양끝에도 공백을 둔 것.

        양끝의 공백은 장식이 아니다. 낱말을 통째로만 맞히기 위한 것이며, 없으면 `일`이 `일정`에 맞아 관계없는
        자료가 답에 섞인다.
        """
        found = self.tokens(text)
        return f" {' '.join(found)} " if found else ""


@lru_cache(maxsize=1)
def analyzer() -> KoreanAnalyzer:
    """한 프로세스에 하나. 모델을 여러 번 열지 않는다."""
    return KoreanAnalyzer()


def install() -> KoreanAnalyzer:
    """검색 질문도 문서와 같은 규칙으로 자르게 한다. 프로세스가 시작할 때 한 번 부른다."""
    from ax_workspace.modules.work.material_extraction import use_analyzer

    found = analyzer()
    use_analyzer(found)
    return found
