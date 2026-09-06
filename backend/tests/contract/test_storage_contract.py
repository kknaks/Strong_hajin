"""자료 저장소는 어디에 있든 같은 약속을 지킨다.

Original bytes will move from a local directory to private object storage. Both live behind one port, so the
contract has to hold for either: what was put comes back exactly, a key that is not there says so, deleting is safe
to repeat, and a key that tries to leave its own space is refused. Pinning it now means the remote adapter has
something to satisfy before an account exists.
"""
from pathlib import Path

import pytest

from ax_workspace.platform.materials import LocalDirectoryMaterialStorage


class InMemoryMaterialStorage:
    """Stands in for a remote object store: keys and bytes, nothing else. Used to pin the shared contract."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        if not key or key.startswith("/") or ".." in key:
            raise ValueError("invalid material storage key")
        self._objects[key] = data

    def get(self, key: str) -> bytes:
        try:
            return self._objects[key]
        except KeyError as error:
            raise FileNotFoundError(key) from error

    def delete(self, key: str) -> None:
        self._objects.pop(key, None)


def _storages(tmp_path: Path):
    return [("local", LocalDirectoryMaterialStorage(tmp_path / "materials")), ("memory", InMemoryMaterialStorage())]


@pytest.mark.parametrize("name", ["local", "memory"])
def test_what_was_put_comes_back_exactly(tmp_path, name: str) -> None:
    storage = dict(_storages(tmp_path))[name]
    key = "tasks/11111111-1111-4111-8111-111111111111/22222222-2222-4222-8222-222222222222"
    payload = "계약서 본문\n납기일 2026-09-30".encode()

    storage.put(key, payload, "text/plain")
    assert storage.get(key) == payload

    # Writing the same key again replaces it; a file version is a new key, so this is only ever a correction.
    storage.put(key, b"corrected", "text/plain")
    assert storage.get(key) == b"corrected"


@pytest.mark.parametrize("name", ["local", "memory"])
def test_a_key_that_is_not_there_says_so(tmp_path, name: str) -> None:
    storage = dict(_storages(tmp_path))[name]
    with pytest.raises(FileNotFoundError):
        storage.get("tasks/33333333-3333-4333-8333-333333333333/44444444-4444-4444-8444-444444444444")


@pytest.mark.parametrize("name", ["local", "memory"])
def test_deleting_is_safe_to_repeat(tmp_path, name: str) -> None:
    """Purging must be able to run again after a partial failure without becoming an error of its own."""
    storage = dict(_storages(tmp_path))[name]
    key = "tasks/55555555-5555-4555-8555-555555555555/66666666-6666-4666-8666-666666666666"
    storage.put(key, "지워질 내용".encode(), "text/plain")

    storage.delete(key)
    storage.delete(key)  # already gone is success
    with pytest.raises(FileNotFoundError):
        storage.get(key)


@pytest.mark.parametrize("name", ["local", "memory"])
def test_a_key_may_not_leave_its_own_space(tmp_path, name: str) -> None:
    storage = dict(_storages(tmp_path))[name]
    for escape in ("../secrets", "/etc/passwd", "tasks/../../etc/passwd"):
        with pytest.raises(ValueError):
            storage.put(escape, b"nope", "text/plain")
