import asyncio
import logging

import structlog

from .config import Settings
from .db import Database
from .service import CoverMonitorService
from .workflow_bot import LeadWorkflowBot


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
    workflow_bot = LeadWorkflowBot(settings, db)
    await workflow_bot.start()
    try:
        await CoverMonitorService(settings, db, workflow_bot).start()
    finally:
        await workflow_bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
