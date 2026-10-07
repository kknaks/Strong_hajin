from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import os
from urllib.parse import urlsplit


#: 「바로 로그인」 목록이 보여 줄 계정의 도메인, 아무 말이 없을 때. 조직마다 다르므로 실행 환경이 정한다.
DEFAULT_DEMO_EMAIL_DOMAIN = "scax.example"

#: 회의 배치 세션이 여는 도구의 바닥 넷 (SCAX-SPEC-004 §7.2-2). 전부 조회 도구다.
DEFAULT_MEETING_AI_TOOLS: tuple[str, ...] = ("task_list", "project_list", "meeting_get", "member_list")


#: Codex 격리 런타임 홈의 기본 자리. `SCAX_CODEX_RUNTIME_HOME` 이 덮어쓴다.
DEFAULT_CODEX_RUNTIME_HOME = ".scax/codex-runtime"

#: `AX_AI_PROVIDER`가 받는 값. 순서가 곧 `Settings.ai_provider`의 기본값(`codex`)이다.
AI_PROVIDERS = ("codex", "claude")

#: 사옥 회의실 예약 시스템의 자리. 참고 구현(mediness-app)의 기본값을 그대로 승격했다 — env 가 덮어쓴다.
DEFAULT_ROOM_BOOKING_BASE_URL = "https://connect.tdl-cloud.com"
#: 우리 회사가 그 시스템에서 갖는 번호. 2026-09-11 실측으로 계정의 소속과 일치함을 확인했다.
DEFAULT_ROOM_BOOKING_COMPANY_ID = 3


#: AI 호출의 단계별 상한(초) 기본값 (SPEC-010 §4.6 · DEC-009 D-12 · OQ-902). provider(Codex·Claude)와 무관하게 같은 값이다.
DEFAULT_AI_TIMEOUT_BATCH_SECONDS = 240
DEFAULT_AI_TIMEOUT_WARMSTART_SECONDS = 240
DEFAULT_AI_TIMEOUT_FINAL_SECONDS = 900
DEFAULT_AI_TIMEOUT_CONVERSATION_SECONDS = 180
#: lease 바닥에 더하는 여유(초) — DB 적재·재배달 판단이 lease 안에서 끝나게.
_FINALIZE_LEASE_MARGIN_SECONDS = 300


def _positive_seconds(name: str, default: int) -> int:
    """timeout env — **양의 정수(초)만**. 숫자가 아니거나 0·음수면 시작 오류로 멈춘다(조용한 기본값 금지 · OQ-1015)."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer (seconds), got {raw!r}") from error
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer (seconds), got {raw!r}")
    return value


def _flag(raw: str | None) -> bool:
    """env 의 불리언 — 말하지 않으면 끔이다. 알림은 켜는 쪽이 위험하므로 기본을 끔에 둔다."""
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _tool_registry(raw: str | None) -> tuple[str, ...]:
    """`AX_MEETING_AI_TOOLS` 는 쉼표로 나눈 도구 이름이다. 비면 바닥 넷만 연다."""
    if not raw:
        return DEFAULT_MEETING_AI_TOOLS
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def _credential(raw: str | None) -> str:
    """OAuth client 값 — 앞뒤 공백·줄바꿈과 **둘러싼 따옴표**를 걷는다. 로컬은 셸이 `. env` 로 따옴표를 벗기지만
    k8s `--from-env-file`·Secret 은 따옴표·끝 줄바꿈을 그대로 실어 같은 파일에서 다른 값이 된다(운영 bad_client_secret 대비)."""
    value = (raw or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return value


def _origin(raw: str, name: str) -> str:
    origin = raw.strip().rstrip("/")
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
        raise ValueError(f"{name} must be an HTTP(S) origin without credentials, path, query or fragment")
    return origin


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
    #: AI 호출의 **단계별** 상한(초) — SPEC-010 §4.6. provider 공장이 단계마다 이 값으로 프로필을 만든다.
    #: 보고서 생성(`generate`)은 대화와 같은 provider 라 대화 값을 따른다.
    ai_timeout_batch_seconds: int = DEFAULT_AI_TIMEOUT_BATCH_SECONDS
    ai_timeout_warmstart_seconds: int = DEFAULT_AI_TIMEOUT_WARMSTART_SECONDS
    ai_timeout_final_seconds: int = DEFAULT_AI_TIMEOUT_FINAL_SECONDS
    ai_timeout_conversation_seconds: int = DEFAULT_AI_TIMEOUT_CONVERSATION_SECONDS
    report_queue_visibility_timeout: int = 120
    report_queue_max_attempts: int = 3
    report_stage_timeout_seconds: int = 300
    report_total_timeout_seconds: int = 900
    report_worker_concurrency: int = 1
    #: 대화·회의 배치·일일보고가 쓰는 CLI provider. `codex`(기본) 또는 `claude` — 어느 쪽이든 같은
    #: `AiProvider` 계약(`generate`/`converse`)을 구현한다. 결제/쿼터 문제로 한쪽이 막혔을 때 코드
    #: 변경 없이 전환하는 자리이지, 응답 품질이나 도구 선택 정책을 바꾸는 자리가 아니다.
    ai_provider: str = "codex"
    #: Codex 가 세션·캐시를 두는 격리 런타임 홈(`CODEX_HOME`). 기본은 작업 디렉터리 아래 상대경로다 —
    #: 프로세스(파드)마다 따로이므로 세션을 다른 프로세스가 이어 쓰려면 같은 자리를 가리켜야 한다.
    codex_runtime_home: str = DEFAULT_CODEX_RUNTIME_HOME
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
    #: 외부 채널 연동 (SPEC-008 §5 「토큰·env」). 외부 서비스 값은 그 서비스 접두어, 내부 값은 `AX_`.
    #: 회의실 예약과 같은 결로 **값이 비면 그 연동이 스스로 없다고 말한다** — 부팅은 멀쩡하다.
    #: 비밀값은 `repr=False` — 설정을 찍는 로그에 값이 실리지 않는다.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = field(default="", repr=False)
    gmail_pubsub_topic: str = ""
    gmail_pubsub_subscription: str = ""
    google_pubsub_sa_key_file: str = ""
    slack_client_id: str = ""
    slack_client_secret: str = field(default="", repr=False)
    #: Socket Mode 앱 토큰. **연동 워커 한 곳만** 쓴다(단일 소유 · SPEC-008 §5 동기화).
    slack_app_token: str = field(default="", repr=False)
    #: 저장 토큰을 잠그는 대칭키 하나(Fernet). 키 회전은 범위 밖(OQ-801).
    external_token_encryption_key: str = field(default="", repr=False)
    #: 카톡 첨부·프로필 이미지 저장본의 hostPath (SPEC-008 §5 저장).
    external_channel_storage_dir: str = ".scax/external-channels"
    #: OAuth 콜백이 닿는 API 의 origin. 운영은 웹과 같은 origin 하나라 비우면 `web_origin` 을 쓴다.
    #: 로컬은 API(8001)와 프론트(5176)가 갈라져 있어 local-stack 이 따로 넘긴다 (SPEC-008 §4.2 redirect_uri).
    api_origin: str = ""

    def __post_init__(self) -> None:
        if self.ai_provider not in AI_PROVIDERS:
            raise ValueError(f"AX_AI_PROVIDER must be one of {AI_PROVIDERS}")
        for name in (
            "ai_timeout_batch_seconds",
            "ai_timeout_warmstart_seconds",
            "ai_timeout_final_seconds",
            "ai_timeout_conversation_seconds",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer (seconds)")
        object.__setattr__(self, "web_origin", _origin(self.web_origin, "AX_WEB_ORIGIN"))
        # API origin 도 같은 검사를 지난다 — 경로·쿼리가 붙으면 OAuth redirect_uri 가 조용히 틀어진다(검수 W-4).
        object.__setattr__(self, "api_origin", _origin(self.api_origin or self.web_origin, "AX_API_ORIGIN"))

    @property
    def gmail_oauth_configured(self) -> bool:
        return bool(self.google_oauth_client_id and self.google_oauth_client_secret)

    @property
    def slack_oauth_configured(self) -> bool:
        return bool(self.slack_client_id and self.slack_client_secret)

    @property
    def room_booking_configured(self) -> bool:
        """계정이 갖춰졌는가. 아니면 예약을 시도조차 하지 않고 사유만 남긴다."""
        return bool(self.room_booking_email and self.room_booking_password)

    @property
    def effective_meeting_finalize_lease_seconds(self) -> int:
        """합성 잡 lease 가 실제로 쓰는 값 — 설정값과 **한 배달이 걸릴 수 있는 최대 시간** 중 큰 쪽.

        한 배달 = 재전사(최대 `retranscribe.TIMEOUT_SECONDS`) + 최종 합성 provider 호출 **최대 횟수**(`FINAL_MAX_PROVIDER_CALLS` —
        시도 3 + 세션 유실 폴백 1 + timeout 뒤 새 세션 1) × 최종 상한 + 여유. timeout 이 아닌 실패(스키마 위반)도 답을 다 낸 뒤라
        한 번이 상한까지 걸릴 수 있다. 워커는 이 값으로 잡고 **heartbeat 로 연장**한다(`meeting_worker`) — 바닥은 연장이 끊겨도
        한 배달의 최악이 lease 안에 드는 안전판이다. lease 가 끝나면 다른 워커가 같은 회의를 집어 재전사·합성이 겹쳐 돈다.
        """
        from ax_workspace.modules.meetings.finalize import FINAL_MAX_PROVIDER_CALLS
        from ax_workspace.modules.meetings.retranscribe import TIMEOUT_SECONDS as RETRANSCRIBE_SECONDS

        floor = (
            RETRANSCRIBE_SECONDS
            + FINAL_MAX_PROVIDER_CALLS * self.ai_timeout_final_seconds
            + _FINALIZE_LEASE_MARGIN_SECONDS
        )
        return max(self.meeting_finalize_lease_seconds, floor)

    @property
    def meeting_ai_tool_registry(self) -> tuple[str, ...]:
        """설정이 준 이름과 최소 넷의 합집합. 순서는 최소 넷이 먼저다 — 프롬프트가 그 순서로 읽힌다."""
        extra = tuple(name for name in self.meeting_ai_tools if name not in DEFAULT_MEETING_AI_TOOLS)
        return (*DEFAULT_MEETING_AI_TOOLS, *extra)

    @property
    def developer_auth_enabled(self) -> bool:
        return self.profile in {RuntimeProfile.DEVELOPMENT, RuntimeProfile.TEST}

    @property
    def demo_shortcuts_enabled(self) -> bool:
        """「바로 로그인」 목록과 데모 비밀번호를 내놓을지. 이메일/비밀번호 로그인 자체는 어느 프로파일에서나 열려 있다 —
        닫히는 것은 모두가 같은 비밀번호를 쓰는 로컬 데모 계정을 나열하는 지름길뿐이다."""
        return self.developer_auth_enabled

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
            ai_timeout_batch_seconds=_positive_seconds("AX_AI_TIMEOUT_BATCH_SECONDS", DEFAULT_AI_TIMEOUT_BATCH_SECONDS),
            ai_timeout_warmstart_seconds=_positive_seconds(
                "AX_AI_TIMEOUT_WARMSTART_SECONDS", DEFAULT_AI_TIMEOUT_WARMSTART_SECONDS
            ),
            ai_timeout_final_seconds=_positive_seconds("AX_AI_TIMEOUT_FINAL_SECONDS", DEFAULT_AI_TIMEOUT_FINAL_SECONDS),
            ai_timeout_conversation_seconds=_positive_seconds(
                "AX_AI_TIMEOUT_CONVERSATION_SECONDS", DEFAULT_AI_TIMEOUT_CONVERSATION_SECONDS
            ),
            report_queue_visibility_timeout=int(os.getenv("AX_REPORT_QUEUE_VISIBILITY_TIMEOUT", "120")),
            report_queue_max_attempts=int(os.getenv("AX_REPORT_QUEUE_MAX_ATTEMPTS", "3")),
            report_stage_timeout_seconds=int(os.getenv("AX_REPORT_STAGE_TIMEOUT_SECONDS", "300")),
            report_total_timeout_seconds=int(os.getenv("AX_REPORT_TOTAL_TIMEOUT_SECONDS", "900")),
            report_worker_concurrency=int(os.getenv("AX_REPORT_WORKER_CONCURRENCY", "1")),
            ai_provider=os.getenv("AX_AI_PROVIDER", "codex"),
            codex_runtime_home=os.getenv("SCAX_CODEX_RUNTIME_HOME") or DEFAULT_CODEX_RUNTIME_HOME,
            demo_email_domain=os.getenv("AX_DEMO_EMAIL_DOMAIN", DEFAULT_DEMO_EMAIL_DOMAIN),
            web_origin=os.getenv("AX_WEB_ORIGIN", "http://localhost:5173"),
            room_booking_base_url=os.getenv("TDL_BASE_URL", DEFAULT_ROOM_BOOKING_BASE_URL),
            room_booking_email=os.getenv("TDL_EMAIL", ""),
            room_booking_password=os.getenv("TDL_PASSWORD", ""),
            room_booking_company_id=int(os.getenv("TDL_COMPANY_ID", str(DEFAULT_ROOM_BOOKING_COMPANY_ID))),
            room_booking_notify=_flag(os.getenv("TDL_NOTIFY")),
            room_booking_timeout_seconds=float(os.getenv("TDL_HTTP_TIMEOUT_SECONDS", "20.0")),
            google_oauth_client_id=_credential(os.getenv("GOOGLE_OAUTH_CLIENT_ID")),
            google_oauth_client_secret=_credential(os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")),
            gmail_pubsub_topic=os.getenv("GMAIL_PUBSUB_TOPIC", ""),
            gmail_pubsub_subscription=os.getenv("GMAIL_PUBSUB_SUBSCRIPTION", ""),
            google_pubsub_sa_key_file=os.getenv("GOOGLE_PUBSUB_SA_KEY_FILE", ""),
            slack_client_id=_credential(os.getenv("SLACK_CLIENT_ID")),
            slack_client_secret=_credential(os.getenv("SLACK_CLIENT_SECRET")),
            slack_app_token=_credential(os.getenv("SLACK_APP_TOKEN")),
            external_token_encryption_key=os.getenv("AX_EXTERNAL_TOKEN_ENCRYPTION_KEY", ""),
            external_channel_storage_dir=os.getenv("AX_EXTERNAL_CHANNEL_STORAGE_DIR", ".scax/external-channels"),
            api_origin=os.getenv("AX_API_ORIGIN", ""),
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
