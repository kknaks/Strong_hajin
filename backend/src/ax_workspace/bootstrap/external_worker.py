"""연동 전용 워커 — 슬랙 Socket Mode · Gmail watch+Pub/Sub pull · 백필 · 재시작 메우기 (WORK-011 BE-2 · SPEC-008 §5).

**레플리카 1 · 단일 소유**(OQ-802): 슬랙 앱 토큰 하나로 Socket Mode 를 이 프로세스 한 곳만 연다. 기존 워커 넷에
얹지 않은 별도 프로세스다. 안에서 도는 것:

- 주 루프 — `ExternalSync.tick()`(백필 한 걸음·메우기·watch 갱신·history 폴링). 할 일이 없으면 쉬되 `ax_external_sync`
  NOTIFY(연결·방 추가) · Pub/Sub 알림이 오면 바로 깬다
- Socket Mode 스레드 — 이벤트를 받아 곧바로 팬아웃 저장
- Pub/Sub pull 스레드 — Gmail 알림의 주소를 「지금 훑을 연동」으로 넘긴다
- LISTEN 스레드 — `ax_external_sync` 를 듣는다(PostgreSQL 일 때만)

값이 없는 쪽은 스스로 쉰다(앱 토큰 없음 = Socket Mode 없음 · SA 키 없음 = 폴링만) — 프로세스는 살아 있어
`make local-stack` 감독을 깨지 않는다.
"""
from __future__ import annotations

import logging
from pathlib import Path
import threading
from typing import Any

from ax_workspace.bootstrap.application import WorkflowApplication
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.modules.external_channels.events import SYNC_WAKE_CHANNEL
from ax_workspace.modules.external_channels.sync import ExternalSync, UpstreamAuthRevoked, UpstreamUnavailable
from ax_workspace.platform.external_channels_sync_store import SqlAlchemyExternalChannelsSyncStore
from ax_workspace.platform.external_gmail import GmailApi
from ax_workspace.platform.external_pubsub import PubSubPuller, qualified
from ax_workspace.platform.external_slack import SlackSocketMode, SlackWebApi
from ax_workspace.platform.persistence import make_session_factory

logger = logging.getLogger(__name__)

#: 할 일이 없을 때 쉬는 상한. NOTIFY·Pub/Sub 알림이 오면 그보다 먼저 깬다.
IDLE_SECONDS = 5.0


class DevelopmentSeamForbidden(RuntimeError):
    """개발 전용 이음새를 운영 프로파일에서 불렀다."""


class ExternalChannelWorker:
    def __init__(self, settings: Settings, *, sync: ExternalSync | None = None, puller: PubSubPuller | None = None) -> None:
        self._settings = settings
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._due: set[str] = set()
        self._due_lock = threading.Lock()
        self._puller = puller if puller is not None else self._make_puller(settings)
        if sync is None:
            cipher = WorkflowApplication._make_external_cipher(settings)
            if cipher is None:
                raise RuntimeError("AX_EXTERNAL_TOKEN_ENCRYPTION_KEY is required to read stored integration tokens")
            topic = settings.gmail_pubsub_topic or None
            if topic and self._puller is not None:
                topic = qualified(topic, self._puller.project_id, "topics")
            sync = ExternalSync(
                SqlAlchemyExternalChannelsSyncStore(make_session_factory(settings.database_url)),
                cipher=cipher,
                slack=SlackWebApi(),
                gmail=GmailApi(settings.google_oauth_client_id, settings.google_oauth_client_secret)
                if settings.gmail_oauth_configured else None,
                gmail_topic=topic if topic and topic.startswith("projects/") else None,
            )
        self._sync = sync

    @staticmethod
    def _make_puller(settings: Settings) -> PubSubPuller | None:
        if not (settings.google_pubsub_sa_key_file and settings.gmail_pubsub_subscription):
            logger.info("gmail pub/sub not configured — history polling only")
            return None
        if not Path(settings.google_pubsub_sa_key_file).is_file():
            logger.warning("GOOGLE_PUBSUB_SA_KEY_FILE does not exist — history polling only")
            return None
        return PubSubPuller(settings.google_pubsub_sa_key_file, settings.gmail_pubsub_subscription)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        threads = [threading.Thread(target=self._listen, name="external-listen", daemon=True)]
        if self._settings.slack_app_token:
            socket = SlackSocketMode(
                self._settings.slack_app_token, on_event=self._sync.handle_slack_event, on_reconnect=self._reconnected
            )
            threads.append(threading.Thread(target=socket.run, args=(self._stop,), name="slack-socket", daemon=True))
        else:
            logger.info("SLACK_APP_TOKEN not set — slack realtime off (backfill only)")
        if self._puller is not None:
            threads.append(threading.Thread(target=self._pull, name="gmail-pubsub", daemon=True))
        for thread in threads:
            thread.start()
        logger.info("external channel worker started")
        failures = 0
        while not self._stop.is_set():
            try:
                busy = self._sync.tick(due_mail=self._take_due())
                failures = 0
            except Exception:  # noqa: BLE001 — 한 바퀴의 실패가 워커를 죽이지 않는다(감독이 스택 전체를 멈춘다)
                failures += 1
                logger.exception("external sync round failed (%d)", failures)
                busy = False
            if not busy:
                self._wake.wait(IDLE_SECONDS if failures == 0 else min(60.0, 2.0 * 2 ** min(failures, 5)))
                self._wake.clear()
        logger.info("external channel worker stopped")

    def _reconnected(self) -> None:
        self._sync.request_gap_fill()
        self._wake.set()

    def _take_due(self) -> set[str]:
        with self._due_lock:
            due, self._due = self._due, set()
        return due

    def _pull(self) -> None:
        assert self._puller is not None
        failures = 0
        while not self._stop.is_set():
            try:
                notes = self._puller.pull()
                failures = 0
            except UpstreamAuthRevoked as error:
                logger.error("pub/sub refused the service account (%s) — history polling only", error.reason)
                return
            except UpstreamUnavailable as error:
                failures += 1
                logger.warning("pub/sub pull failed (%s); retry %d", error, failures)
                self._stop.wait(min(60.0, 2.0 * 2 ** min(failures, 5)))
                continue
            due: set[str] = set()
            for note in notes:
                if note.email:
                    due.update(self._sync.mail_due(note.email))
            if due:
                with self._due_lock:
                    self._due |= due
                self._wake.set()
            # 알림은 「훑어라」 신호일 뿐이라 바로 ack 한다 — 놓쳐도 주기 폴링이 같은 history 를 받는다.
            try:
                self._puller.acknowledge([note.ack_id for note in notes])
            except UpstreamUnavailable as error:
                logger.warning("pub/sub ack failed (%s)", error)

    def _listen(self) -> None:
        url = self._settings.database_url
        if not url.startswith("postgresql"):
            return
        import psycopg

        dsn = url.replace("postgresql+psycopg://", "postgresql://", 1)
        while not self._stop.is_set():
            try:
                with psycopg.connect(dsn, autocommit=True) as connection:
                    connection.execute(f"LISTEN {SYNC_WAKE_CHANNEL}")
                    while not self._stop.is_set():
                        for _ in connection.notifies(timeout=1.0):
                            self._wake.set()
            except psycopg.Error as error:
                logger.warning("listen on %s dropped (%s)", SYNC_WAKE_CHANNEL, type(error).__name__)
                self._stop.wait(5.0)


def connect_dev_slack(settings: Settings, member_id: str, token: str, *, identity: Any = None) -> dict[str, str]:
    """**개발 전용** — 실측 때 받은 사용자 토큰을 「연결된 슬랙 연동」으로 넣는다(4차 검수 ★3).

    슬랙 OAuth 콜백은 https 라 운영에서만 된다. 이 이음새로 로컬에서 Socket Mode 수신·팬아웃·답장을 잰다.
    `X-Demo-Persona` 와 같은 결: **운영 프로파일이면 아무것도 하기 전에 거절한다.**
    """
    if settings.profile is RuntimeProfile.PRODUCTION or not settings.developer_auth_enabled:
        raise DevelopmentSeamForbidden("the development slack token seam is forbidden in production")
    token = token.strip()
    if not token.startswith("xoxp-"):
        raise ValueError("a slack user token (xoxp-) is required")
    who = (identity or SlackWebApi.identity)(token)
    cipher = WorkflowApplication._make_external_cipher(settings)
    if cipher is None:
        raise RuntimeError("no token encryption key")
    store = SqlAlchemyExternalChannelsSyncStore(make_session_factory(settings.database_url))
    integration_id = store.connect_slack_for_development(
        member_id=member_id, team_id=str(who["team_id"]), team_name=str(who.get("team") or who["team_id"]),
        user_id=who.get("user_id"), access_token_encrypted=cipher.encrypt(token), scopes=str(who.get("_scopes") or ""),
    )
    return {"integration_id": integration_id, "team": str(who.get("team") or who["team_id"])}
