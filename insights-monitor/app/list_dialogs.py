import asyncio

from telethon import TelegramClient, utils

from .config import Settings


async def main():
    settings = Settings()
    client = TelegramClient(
        settings.telegram_session, settings.telegram_api_id, settings.telegram_api_hash
    )
    await client.start()
    me = await client.get_me()
    print(f"ACCOUNT: {me.first_name} (@{me.username or '-'}, id={me.id})")
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        print(
            f"{utils.get_peer_id(entity):<16} {type(entity).__name__:<16} "
            f"@{getattr(entity, 'username', None) or '-':<24} {dialog.name}"
        )
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
