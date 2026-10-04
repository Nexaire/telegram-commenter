import asyncio
import logging

import structlog

from .config import Settings
from .db import Database
from .service import InsightsMonitor


async def main():
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    db = Database(settings.database_path)
    await db.init()
    await InsightsMonitor(settings, db).start()


if __name__ == "__main__":
    asyncio.run(main())
