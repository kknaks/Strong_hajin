"""저장 토큰의 가역 암호화 — env 대칭키 하나(Fernet · SPEC-008 §4 Data Contract · OQ-801).

비밀번호(`credentials.py`)는 되돌릴 일이 없어 해시지만 외부 채널 토큰은 수집·중계·답장 때 다시 써야 해서 잠갔다가
연다. 키는 `AX_EXTERNAL_TOKEN_ENCRYPTION_KEY` 하나 — 회전은 범위 밖이다. 평문 토큰은 이 모듈 밖으로 로그·예외
메시지에 실리지 않는다.
"""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class TokenDecryptionFailed(Exception):
    """키가 바뀌었거나 값이 깨졌다. 어떤 값이었는지는 말하지 않는다."""


class FernetTokenCipher:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            raise ValueError("AX_EXTERNAL_TOKEN_ENCRYPTION_KEY must be a urlsafe-base64 32-byte Fernet key") from None

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            raise TokenDecryptionFailed("stored external token could not be decrypted") from None


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


def development_key(path: Path) -> str:
    """개발 기기 전용 — env 에 키가 없으면 작업 디렉터리의 `.scax/` 아래 키 파일 하나를 만들어 다시 쓴다.

    `make local-stack` 을 껐다 켜도 저장 토큰이 그대로 열리게 하는 것뿐이다. 운영 프로파일은 이 길을 타지 않고
    키가 없으면 연동이 스스로 없다고 말한다(조립층이 가른다).
    """
    if path.is_file():
        return _read_key(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 다 쓴 임시 파일을 제자리에 «걸어» 둔다 — 같은 자리를 동시에 만드는 프로세스가 있어도 한쪽 키만 남고,
    # 반쯤 쓴 파일을 읽는 일이 없다.
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(generate_key())
    try:
        os.link(temporary, path)
    except FileExistsError:
        pass
    finally:
        temporary.unlink(missing_ok=True)
    return _read_key(path)


def _read_key(path: Path) -> str:
    return path.read_text(encoding="ascii").strip()
