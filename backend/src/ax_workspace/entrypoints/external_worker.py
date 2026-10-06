"""연동 전용 워커 진입점 — 레플리카 1 (SPEC-008 §5 프로세스 배치 · OQ-802)."""
import logging
import signal

from ax_workspace.bootstrap.external_worker import ExternalChannelWorker
from ax_workspace.bootstrap.settings import Settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    worker = ExternalChannelWorker(Settings.from_environment())
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: worker.stop())
    worker.run()


if __name__ == "__main__":
    main()
