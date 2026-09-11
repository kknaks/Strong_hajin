"""검색이 글자를 비교하기 전에 같은 모양으로 맞추는 한 곳.

한글은 컴퓨터 안에서 두 가지로 적힐 수 있다. macOS Finder가 주는 이름과 거기서 복사한 글자는 자모가 풀린
모양(NFD)이고, 사람이 키보드로 친 글자와 데이터베이스에 들어 있는 것은 합쳐진 모양(NFC)이다. 눈에는 똑같이
보이지만 코드 포인트가 다르므로 `한빛 in 한빛의원`이 거짓이 된다.

전달받은 자료를 분류할 때 같은 문제를 이미 겪었다. 그때는 파일 이름만 맞췄고 검색은 그대로 두었는데, 그러면
Finder에서 복사한 이름으로 찾는 사람에게 아무것도 나오지 않는다. 비교하는 자리를 한 곳으로 모아 둔다.
"""
from __future__ import annotations

import unicodedata


def normalize(text: str) -> str:
    """비교하기 전의 한 가지 모양. 보이는 글자는 그대로이고 적히는 방식만 하나로 맞춘다."""
    return unicodedata.normalize("NFC", str(text or ""))


def folded(text: str) -> str:
    """대소문자와 자모 조합을 함께 지운 비교용 형태."""
    return normalize(text).lower()


def matches(query: str, text: str) -> bool:
    """제목 검색이 묻는 것: 이 글자들이 저 안에 있는가. 질의와 본문을 같은 모양으로 맞춘 뒤에 본다."""
    return folded(query) in folded(text)


_PERSON_REFERENCE_PREFIXES = ("우리 ", "저희 ", "내 ", "제 ")


def normalize_person_reference(text: str) -> str:
    """사람·직책 검색에서만 제거해도 의미가 바뀌지 않는 한국어 지칭과 존칭을 걷어낸다.

    직책 alias를 추측하지 않는다. `팀장님`의 `님`은 말투지만 `팀장`은 조직 원장의 실제 직책명이고,
    그 이름을 어떤 사람과 연결할지는 appointment가 결정한다.
    """
    reference = " ".join(normalize(text).split())
    for prefix in _PERSON_REFERENCE_PREFIXES:
        if reference.startswith(prefix):
            reference = reference[len(prefix):].strip()
            break
    if reference.endswith(" 님"):
        reference = reference[:-2].rstrip()
    elif len(reference) > 1 and reference.endswith("님"):
        reference = reference[:-1].rstrip()
    return reference
