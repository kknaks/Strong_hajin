"""Local password credentials.

A password is never stored, compared, or logged: what is kept is a salted PBKDF2-SHA256 digest, and every failure —
unknown address, wrong password, membership that has ended — is answered identically so that trying addresses tells an
attacker nothing about who works here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import os
from base64 import b64decode, b64encode

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 240_000
MINIMUM_PASSWORD_LENGTH = 8


class AuthenticationFailed(Exception):
    """The credential was not accepted. It deliberately never says which part was wrong."""


class PasswordRejected(ValueError):
    """The proposed password does not meet the minimum policy."""


@dataclass(frozen=True, slots=True)
class LocalCredential:
    member_id: str
    email: str
    password_hash: str


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str, *, salt: bytes | None = None, iterations: int = ITERATIONS) -> str:
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise PasswordRejected(f"비밀번호는 최소 {MINIMUM_PASSWORD_LENGTH}자여야 합니다")
    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${b64encode(salt).decode()}${b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$")
        if algorithm != ALGORITHM:
            return False
        expected = b64decode(raw_digest)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b64decode(raw_salt), int(raw_iterations))
    except (AttributeError, ValueError):
        return False
    return hmac.compare_digest(candidate, expected)
