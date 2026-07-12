import asyncio
import logging

import structlog

from .bot import ApprovalBot
from .config import Settings
from .db import Database
from .llm import CommentGenerator
from .service import CommenterService


async def main():
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    structlog.configure(processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()])
    db = Database(settings.database_path)
    await db.init()
    generator = CommentGenerator(settings)
    bot = ApprovalBot(settings, db, generator)
    service = CommenterService(settings, db, generator, bot)
    await bot.start()
    try:
        await service.start()
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
