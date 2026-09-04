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
    job_queue_backend: str = "memory"
    materials_dir: str = ".scax/materials"
    recordings_dir: str = ".scax/recordings"
    material_queue_visibility_timeout: int = 120
    material_queue_max_attempts: int = 3
    material_worker_concurrency: int = 2

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
            job_queue_backend=_job_queue_backend_from_environment(),
            materials_dir=os.getenv("AX_MATERIALS_DIR", ".scax/materials"),
            recordings_dir=os.getenv("AX_RECORDINGS_DIR", ".scax/recordings"),
            material_queue_visibility_timeout=int(os.getenv("AX_MATERIAL_QUEUE_VISIBILITY_TIMEOUT", "120")),
            material_queue_max_attempts=int(os.getenv("AX_MATERIAL_QUEUE_MAX_ATTEMPTS", "3")),
            material_worker_concurrency=int(os.getenv("AX_MATERIAL_WORKER_CONCURRENCY", "2")),
        )


JOB_QUEUE_BACKENDS = ("postgres", "memory")


def _job_queue_backend_from_environment() -> str:
    """Fail fast on the removed PGMQ transport or its legacy variable instead of silently falling back."""
    legacy = os.getenv("AX_CONVERSATION_QUEUE_BACKEND")
    if legacy is not None:
        raise RuntimeError(
            "AX_CONVERSATION_QUEUE_BACKEND is no longer supported (PGMQ was removed). "
            "Set AX_JOB_QUEUE_BACKEND=postgres for the shared durable job table, or memory for in-process tests."
        )
    value = os.getenv("AX_JOB_QUEUE_BACKEND", "postgres").strip().lower()
    if value not in JOB_QUEUE_BACKENDS:
        raise RuntimeError(
            f"AX_JOB_QUEUE_BACKEND={value!r} is not supported; use one of {', '.join(JOB_QUEUE_BACKENDS)}. "
            "The pgmq transport was removed and null is only an explicit in-code test configuration."
        )
    return value
