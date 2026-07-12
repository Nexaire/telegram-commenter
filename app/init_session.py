import asyncio
from pathlib import Path

from telethon import TelegramClient

from .config import Settings


async def main():
    settings = Settings()
    Path(settings.telegram_session).parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(settings.telegram_session, settings.telegram_api_id, settings.telegram_api_hash)
    await client.start()
    me = await client.get_me()
    print(f"Session created for Telegram user {me.id} ({me.username or me.first_name})")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

