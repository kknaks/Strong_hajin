from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
from urllib.parse import urlsplit


#: 「바로 로그인」 목록이 보여 줄 계정의 도메인, 아무 말이 없을 때. 조직마다 다르므로 실행 환경이 정한다.
DEFAULT_DEMO_EMAIL_DOMAIN = "scax.example"

#: 회의 배치 세션이 여는 도구의 바닥 넷 (SCAX-SPEC-004 §7.2-2). 전부 조회 도구다.
DEFAULT_MEETING_AI_TOOLS: tuple[str, ...] = ("task_list", "project_list", "meeting_get", "member_list")


#: `AX_AI_PROVIDER`가 받는 값. 순서가 곧 `Settings.ai_provider`의 기본값(`codex`)이다.
AI_PROVIDERS = ("codex", "claude")

#: 사옥 회의실 예약 시스템의 자리. 참고 구현(mediness-app)의 기본값을 그대로 승격했다 — env 가 덮어쓴다.
DEFAULT_ROOM_BOOKING_BASE_URL = "https://connect.tdl-cloud.com"
#: 우리 회사가 그 시스템에서 갖는 번호. 2026-09-11 실측으로 계정의 소속과 일치함을 확인했다.
DEFAULT_ROOM_BOOKING_COMPANY_ID = 3


def _flag(raw: str | None) -> bool:
    """env 의 불리언 — 말하지 않으면 끔이다. 알림은 켜는 쪽이 위험하므로 기본을 끔에 둔다."""
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _tool_registry(raw: str | None) -> tuple[str, ...]:
    """`AX_MEETING_AI_TOOLS` 는 쉼표로 나눈 도구 이름이다. 비면 바닥 넷만 연다."""
    if not raw:
        return DEFAULT_MEETING_AI_TOOLS
    return tuple(name.strip() for name in raw.split(",") if name.strip())


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
    # 오디오 원본이 사는 자리. 중계 경로가 회의당 파일 하나에 덧붙인다 (SCAX-SPEC-004 §5.5).
    recordings_dir: str = ".scax/recordings"
    material_queue_visibility_timeout: int = 120
    material_queue_max_attempts: int = 3
    material_stage_timeout_seconds: int = 120
    material_total_timeout_seconds: int = 600
    material_worker_concurrency: int = 2
    #: 회의 중 AI 배치의 트리거 수치 (SCAX-SPEC-004 §13 `OQ-307` 잠정값 — 실측으로 갈아 끼운다).
    meeting_batch_chars: int = 600
    meeting_batch_switch_min_chars: int = 80
    meeting_batch_max_wait_seconds: int = 90
    #: **도구 레지스트리** — 배치 세션에 여는 도구 이름. 최소 넷은 SPEC §7.2-2 가 못박고, 이 값이 그 위에 더한다.
    #: 바닥이지 천장이 아니다: 설정으로 늘린다. 전부 조회 도구여야 한다.
    meeting_ai_tools: tuple[str, ...] = DEFAULT_MEETING_AI_TOOLS
    #: 합성 잡의 lease. **재전사 상한(1200초)보다 커야 한다** — 종료 파이프라인이 ① 재전사 → ② 합성
    #: 순으로 한 배달 안에서 돌기 때문이다. 짧으면 긴 음원을 다시 듣는 동안 lease 가 먼저 만료되고,
    #: 다른 워커가 같은 회의를 집어 재전사가 두 번 돈다.
    meeting_finalize_lease_seconds: int = 1800
    report_queue_visibility_timeout: int = 120
    report_queue_max_attempts: int = 3
    report_stage_timeout_seconds: int = 300
    report_total_timeout_seconds: int = 900
    report_worker_concurrency: int = 1
    #: 대화·회의 배치·일일보고가 쓰는 CLI provider. `codex`(기본) 또는 `claude` — 어느 쪽이든 같은
    #: `AiProvider` 계약(`generate`/`converse`)을 구현한다. 결제/쿼터 문제로 한쪽이 막혔을 때 코드
    #: 변경 없이 전환하는 자리이지, 응답 품질이나 도구 선택 정책을 바꾸는 자리가 아니다.
    ai_provider: str = "codex"
    #: 이 도메인의 계정만 「바로 로그인」 목록에 오른다 — 그 밖의 실제 계정은 로컬 DB에 있어도 나열되지 않는다.
    demo_email_domain: str = DEFAULT_DEMO_EMAIL_DOMAIN
    web_origin: str = "http://localhost:5173"
    #: 사옥 회의실 예약 시스템 (SCAX-WP-007). 계정 둘은 **값이 비면 기능이 스스로 없다고 말한다** —
    #: 부팅은 멀쩡하고, 예약을 부르는 순간에만 사유가 남는다. 값을 코드에 두지 않는다.
    room_booking_base_url: str = DEFAULT_ROOM_BOOKING_BASE_URL
    room_booking_email: str = ""
    room_booking_password: str = ""
    room_booking_company_id: int = DEFAULT_ROOM_BOOKING_COMPANY_ID
    #: 예약 시스템이 참석자에게 알림을 보낼지. 기본은 끔 — 데모가 실제 사람에게 메일을 쏘지 않는다.
    room_booking_notify: bool = False
    #: 한 번의 왕복 상한(초). [만들기] 가 이 값만큼 기다릴 수 있다 — 그래서 짧다.
    room_booking_timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        if self.ai_provider not in AI_PROVIDERS:
            raise ValueError(f"AX_AI_PROVIDER must be one of {AI_PROVIDERS}")
        origin = self.web_origin.strip().rstrip("/")
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "AX_WEB_ORIGIN must be an HTTP(S) origin without credentials, path, query or fragment"
            )
        object.__setattr__(self, "web_origin", origin)

    @property
    def room_booking_configured(self) -> bool:
        """계정이 갖춰졌는가. 아니면 예약을 시도조차 하지 않고 사유만 남긴다."""
        return bool(self.room_booking_email and self.room_booking_password)

    @property
    def meeting_ai_tool_registry(self) -> tuple[str, ...]:
        """설정이 준 이름과 최소 넷의 합집합. 순서는 최소 넷이 먼저다 — 프롬프트가 그 순서로 읽힌다."""
        extra = tuple(name for name in self.meeting_ai_tools if name not in DEFAULT_MEETING_AI_TOOLS)
        return (*DEFAULT_MEETING_AI_TOOLS, *extra)

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
            material_stage_timeout_seconds=int(os.getenv("AX_MATERIAL_STAGE_TIMEOUT_SECONDS", "120")),
            material_total_timeout_seconds=int(os.getenv("AX_MATERIAL_TOTAL_TIMEOUT_SECONDS", "600")),
            material_worker_concurrency=int(os.getenv("AX_MATERIAL_WORKER_CONCURRENCY", "2")),
            meeting_batch_chars=int(os.getenv("AX_MEETING_BATCH_CHARS", "600")),
            meeting_batch_switch_min_chars=int(os.getenv("AX_MEETING_BATCH_SWITCH_MIN_CHARS", "80")),
            meeting_batch_max_wait_seconds=int(os.getenv("AX_MEETING_BATCH_MAX_WAIT_SECONDS", "90")),
            meeting_ai_tools=_tool_registry(os.getenv("AX_MEETING_AI_TOOLS")),
            meeting_finalize_lease_seconds=int(os.getenv("AX_MEETING_FINALIZE_LEASE_SECONDS", "1800")),
            report_queue_visibility_timeout=int(os.getenv("AX_REPORT_QUEUE_VISIBILITY_TIMEOUT", "120")),
            report_queue_max_attempts=int(os.getenv("AX_REPORT_QUEUE_MAX_ATTEMPTS", "3")),
            report_stage_timeout_seconds=int(os.getenv("AX_REPORT_STAGE_TIMEOUT_SECONDS", "300")),
            report_total_timeout_seconds=int(os.getenv("AX_REPORT_TOTAL_TIMEOUT_SECONDS", "900")),
            report_worker_concurrency=int(os.getenv("AX_REPORT_WORKER_CONCURRENCY", "1")),
            ai_provider=os.getenv("AX_AI_PROVIDER", "codex"),
            demo_email_domain=os.getenv("AX_DEMO_EMAIL_DOMAIN", DEFAULT_DEMO_EMAIL_DOMAIN),
            web_origin=os.getenv("AX_WEB_ORIGIN", "http://localhost:5173"),
            room_booking_base_url=os.getenv("TDL_BASE_URL", DEFAULT_ROOM_BOOKING_BASE_URL),
            room_booking_email=os.getenv("TDL_EMAIL", ""),
            room_booking_password=os.getenv("TDL_PASSWORD", ""),
            room_booking_company_id=int(os.getenv("TDL_COMPANY_ID", str(DEFAULT_ROOM_BOOKING_COMPANY_ID))),
            room_booking_notify=_flag(os.getenv("TDL_NOTIFY")),
            room_booking_timeout_seconds=float(os.getenv("TDL_HTTP_TIMEOUT_SECONDS", "20.0")),
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
