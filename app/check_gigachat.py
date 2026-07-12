import asyncio

from gigachat import GigaChat

from .config import Settings


async def main():
    settings = Settings()
    async with GigaChat(
        credentials=settings.gigachat_credentials,
        scope=settings.gigachat_scope,
        model=settings.gigachat_model,
        base_url=settings.gigachat_base_url,
        verify_ssl_certs=settings.gigachat_verify_ssl_certs,
        ca_bundle_file=settings.gigachat_ca_bundle_file or None,
        timeout=60,
    ) as client:
        response = await client.achat("Ответь одним словом: работает")
    print("GigaChat connection OK")
    print("Model response:", response.choices[0].message.content)


if __name__ == "__main__":
    asyncio.run(main())

