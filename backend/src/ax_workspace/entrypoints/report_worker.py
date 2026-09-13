import asyncio

from ax_workspace.bootstrap.report_worker import DailyReportGenerationWorker
from ax_workspace.bootstrap.settings import Settings


def main() -> None:
    asyncio.run(DailyReportGenerationWorker(Settings.from_environment()).run())


if __name__ == "__main__":
    main()
