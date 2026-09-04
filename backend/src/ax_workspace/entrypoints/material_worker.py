"""Process entrypoint for the bootstrap-composed material extraction worker."""
from __future__ import annotations

import asyncio
import signal

from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
from ax_workspace.bootstrap.settings import Settings


async def _main() -> None:
    worker = MaterialExtractionWorker(Settings.from_environment())
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, worker.stop)
    await worker.run()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
