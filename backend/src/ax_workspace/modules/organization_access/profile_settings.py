"""프로필 설정 — 이미지(즉시 저장)·비밀번호 변경 (SPEC-008 §2.6·§4.7 · WORK-011 BE-3).

- 이름·소속·직책·직무는 **명부 값, 읽기 전용**이다 — 여기서 고치지 않는다. 머리 값은 `GET /api/organization/me` 를 재사용하고
  `profile_image_url` 한 칸만 더한다(W-15).
- 이미지: 고르면 바로 저장 · **1MB 이하 PNG·JPG** — 형식은 선언이 아니라 **바이트 머리(magic)** 로 판정한다. 바이트는
  hostPath(`AX_EXTERNAL_CHANNEL_STORAGE_DIR`), DB 는 경로만.
- 비밀번호: 현재 비밀번호를 `verify_password` 로 확인(틀리면 전용 오류 401) · 새 것은 `hash_password`(≥8자,
  `PasswordRejected` 422) · 바꾸면 **이 세션만 남기고 그 회원의 다른 로그인 세션을 모두 해제** + **기기 토큰도 모두 철회**
  (R3-F1 ⑤ — 수집기는 다시 「이 Mac 연결」). 비밀번호·해시는 로그·응답에 싣지 않는다(`credentials.py` 머리 주석의 결).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict

from ax_workspace.modules.errors import ResourceNotFound
from ax_workspace.modules.organization_access.credentials import hash_password, verify_password
from ax_workspace.modules.organization_access.domain import Principal

PROFILE_IMAGE_LIMIT_BYTES = 1024 * 1024
PROFILE_IMAGE_PATH = "/api/profile/image"


class ProfileImageTooLarge(ValueError):
    """1MB 초과 — 413."""


class ProfileImageUnsupported(ValueError):
    """PNG·JPG 가 아니다 — 415."""


class ProfileImageMissing(LookupError, ResourceNotFound):
    """올린 이미지가 없다 — 404(이니셜 아바타로 그린다)."""


class CurrentPasswordIncorrect(Exception):
    """현재 비밀번호가 틀렸다 — 401 전용 오류(로그인 실패와 다른 코드)."""


class PasswordChangeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current: str = Field(min_length=1, max_length=1000)
    new: str = Field(min_length=1, max_length=1000)


class ProfileImageSaved(TypedDict):
    profile_image_url: str


class ProfileSettingsRepository(Protocol):
    def profile_image(self, member_id: str) -> Any | None: ...
    def save_profile_image(self, member_id: str, *, storage_key: str, content_type: str, size: int, at: datetime) -> Any: ...
    def delete_profile_image(self, member_id: str) -> None: ...
    def password_hash(self, member_id: str) -> str | None: ...
    def set_password_hash(self, member_id: str, password_hash: str) -> None: ...
    def revoke_other_sessions(self, member_id: str, keep_session_id: UUID | None, at: datetime) -> int: ...


class DeviceTokenRevoker(Protocol):
    def revoke_device_tokens(self, member_id: str, at: datetime) -> int: ...


class BlobStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


def sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def profile_image_url(record: Any | None) -> str | None:
    """바뀔 때마다 주소가 달라져 캐시가 옛 그림을 붙들지 않는다."""
    if record is None:
        return None
    updated = record.updated_at if record.updated_at.tzinfo else record.updated_at.replace(tzinfo=UTC)
    return f"{PROFILE_IMAGE_PATH}?v={int(updated.timestamp() * 1000)}"


class ProfileSettingsApplication:
    def __init__(
        self,
        repository: ProfileSettingsRepository,
        *,
        device_tokens: DeviceTokenRevoker,
        storage: BlobStorage,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._device_tokens = device_tokens
        self._storage = storage
        self._clock = clock

    def image_url(self, member_id: str) -> str | None:
        return profile_image_url(self._repository.profile_image(member_id))

    def save_image(self, principal: Principal, data: bytes) -> ProfileImageSaved:
        if len(data) > PROFILE_IMAGE_LIMIT_BYTES:
            raise ProfileImageTooLarge("프로필 이미지는 1MB 이하입니다")
        content_type = sniff_image(data)
        if content_type is None:
            raise ProfileImageUnsupported("PNG·JPG 만 올릴 수 있습니다")
        member_id = str(principal.id)
        previous = self._repository.profile_image(member_id)
        old_key = previous.storage_key if previous is not None else None
        key = f"profiles/{member_id}/{uuid4()}"
        self._storage.put(key, data, content_type)
        record = self._repository.save_profile_image(
            member_id, storage_key=key, content_type=content_type, size=len(data), at=self._clock()
        )
        if old_key and old_key != key:
            self._storage.delete(old_key)
        return {"profile_image_url": profile_image_url(record) or PROFILE_IMAGE_PATH}

    def image(self, principal: Principal) -> tuple[bytes, str]:
        record = self._repository.profile_image(str(principal.id))
        if record is None:
            raise ProfileImageMissing("profile image was not found")
        try:
            return self._storage.get(record.storage_key), record.content_type
        except FileNotFoundError:
            raise ProfileImageMissing("profile image bytes are missing") from None

    def delete_image(self, principal: Principal) -> None:
        member_id = str(principal.id)
        record = self._repository.profile_image(member_id)
        if record is None:
            return
        key = record.storage_key
        self._repository.delete_profile_image(member_id)
        self._storage.delete(key)

    def change_password(self, principal: Principal, command: PasswordChangeCommand, *, keep_session_id: UUID | None) -> None:
        member_id = str(principal.id)
        stored = self._repository.password_hash(member_id)
        if stored is None or not verify_password(command.current, stored):
            raise CurrentPasswordIncorrect("현재 비밀번호가 올바르지 않습니다")
        self._repository.set_password_hash(member_id, hash_password(command.new))
        now = self._clock()
        self._repository.revoke_other_sessions(member_id, keep_session_id, now)
        self._device_tokens.revoke_device_tokens(member_id, now)
