"""Private, anonymous temporary storage for extraction output between parse and publish.

Only one record is deserialized at a time. Each iterator owns its offset, so blocks and chunks can be consumed
without materializing the entire projection. The worker closes both spools even when publication fails.
"""
from collections.abc import Iterator, Sequence
from dataclasses import asdict
from itertools import islice
import json
from tempfile import TemporaryFile
from typing import Generic, TypeVar

T = TypeVar("T")


class ExtractionSpool(Sequence[T], Generic[T]):
    def __init__(self, record_type: type[T]) -> None:
        self._record_type = record_type
        self._file = TemporaryFile(mode="w+b")
        self._count = 0

    def append(self, record: T) -> None:
        self._file.seek(0, 2)
        self._file.write(json.dumps(asdict(record), ensure_ascii=False).encode("utf-8") + b"\n")
        self._count += 1

    def __len__(self) -> int:
        return self._count

    def __iter__(self) -> Iterator[T]:
        offset = 0
        for _ in range(self._count):
            self._file.seek(offset)
            line = self._file.readline()
            offset = self._file.tell()
            yield self._record_type(**json.loads(line))

    def __getitem__(self, index):
        if isinstance(index, slice):
            return list(self)[index]
        if index < 0:
            index += self._count
        if index < 0 or index >= self._count:
            raise IndexError(index)
        return next(islice(self, index, None))

    def close(self) -> None:
        self._file.close()
