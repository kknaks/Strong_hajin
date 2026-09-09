from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os


#: 「바로 로그인」 목록이 보여 줄 계정의 도메인, 아무 말이 없을 때. 조직마다 다르므로 실행 환경이 정한다.
DEFAULT_DEMO_EMAIL_DOMAIN = "scax.example"


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
    meeting_queue_visibility_timeout: int = 120
    meeting_queue_max_attempts: int = 3
    meeting_worker_concurrency: int = 1
    #: 이 도메인의 계정만 「바로 로그인」 목록에 오른다 — 그 밖의 실제 계정은 로컬 DB에 있어도 나열되지 않는다.
    demo_email_domain: str = DEFAULT_DEMO_EMAIL_DOMAIN

    @property
    def developer_auth_enabled(self) -> bool:
        return self.profile in {RuntimeProfile.DEVELOPMENT, RuntimeProfile.TEST}

    @property
    def local_login_enabled(self) -> bool:
        """Email/password sign-in. Production proves identity through an external provider instead."""
        return self.profile is not RuntimeProfile.PRODUCTION

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
            meeting_queue_visibility_timeout=int(os.getenv("AX_MEETING_QUEUE_VISIBILITY_TIMEOUT", "120")),
            meeting_queue_max_attempts=int(os.getenv("AX_MEETING_QUEUE_MAX_ATTEMPTS", "3")),
            meeting_worker_concurrency=int(os.getenv("AX_MEETING_WORKER_CONCURRENCY", "1")),
            demo_email_domain=os.getenv("AX_DEMO_EMAIL_DOMAIN", DEFAULT_DEMO_EMAIL_DOMAIN),
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
