"""Process entrypoint for Meeting recording finalization."""
from __future__ import annotations

import asyncio
import signal

from ax_workspace.bootstrap.meeting_worker import MeetingFinalizationWorker
from ax_workspace.bootstrap.settings import Settings


async def _main() -> None:
    worker = MeetingFinalizationWorker(Settings.from_environment())
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, worker.stop)
    await worker.run()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
