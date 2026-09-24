import asyncio

from telethon import TelegramClient, utils

from .config import Settings


def entity_kind(entity) -> str:
    if getattr(entity, "broadcast", False):
        return "channel"
    if getattr(entity, "megagroup", False):
        return "supergroup"
    if entity.__class__.__name__ == "Chat":
        return "group"
    return "user"


async def main():
    settings = Settings()
    client = TelegramClient(
        settings.telegram_session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError(
            "Telegram session is not authorized. Run python -m app.init_session first."
        )
    print("PEER_ID\tTYPE\tUSERNAME\tTITLE")
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        username = getattr(entity, "username", None)
        username_text = f"@{username}" if username else "-"
        print(
            f"{utils.get_peer_id(entity)}\t{entity_kind(entity)}\t"
            f"{username_text}\t{dialog.name}"
        )
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
