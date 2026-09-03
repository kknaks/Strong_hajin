from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os


class RuntimeProfile(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


@dataclass(frozen=True, slots=True)
class Settings:
    profile: RuntimeProfile
    database_url: str
    conversation_queue_max_fragments: int = 8
    conversation_queue_visibility_timeout: int = 120
    conversation_queue_max_attempts: int = 3
    conversation_worker_concurrency: int = 4
    conversation_queue_backend: str = "null"
    materials_dir: str = ".scax/materials"

    @property
    def developer_auth_enabled(self) -> bool:
        return self.profile in {RuntimeProfile.DEVELOPMENT, RuntimeProfile.TEST}

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            profile=RuntimeProfile(os.getenv("AX_PROFILE", RuntimeProfile.DEVELOPMENT)),
            database_url=os.getenv(
                "DATABASE_URL", "postgresql+psycopg://ax:ax@localhost:5432/ax_demo"
            ),
            conversation_queue_max_fragments=int(os.getenv("AX_CONVERSATION_QUEUE_MAX_FRAGMENTS", "8")),
            conversation_queue_visibility_timeout=int(
                os.getenv("AX_CONVERSATION_QUEUE_VISIBILITY_TIMEOUT", "120")
            ),
            conversation_queue_max_attempts=int(
                os.getenv("AX_CONVERSATION_QUEUE_MAX_ATTEMPTS", "3")
            ),
            conversation_worker_concurrency=int(
                os.getenv("AX_CONVERSATION_WORKER_CONCURRENCY", "4")
            ),
            conversation_queue_backend=os.getenv("AX_CONVERSATION_QUEUE_BACKEND", "pgmq"),
            materials_dir=os.getenv("AX_MATERIALS_DIR", ".scax/materials"),
        )
