import asyncio
import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

import aiohttp


SYSTEM_PROMPT = """Ты анализируешь русскоязычные сообщения в сообществах поддержки.
Найди повторяемую человеческую проблему, тему или потребность, полезную для
планирования просветительских статей и постов. Не ставь диагноз, не давай
лечение и не делай выводов о человеке сверх написанного. Не включай имя,
контакты или иные идентификаторы. Не выдумывай контекст.

Верни только JSON-объект со строковыми полями:
relevant ("true" или "false"), topic, pain, situation, need,
content_angles (массив из 1-3 коротких тем для материалов), confidence
(число от 0 до 1). Если сообщение не описывает личную или обсуждаемую
проблему/потребность, relevant=false.
"""


class GigaChatAnalyzer:
    def __init__(self, settings):
        self.settings = settings
        self._access_token: str | None = None
        self._token_expires_at = datetime.min.replace(tzinfo=timezone.utc)
        self._session: aiohttp.ClientSession | None = None

    async def start(self):
        timeout = aiohttp.ClientTimeout(total=60)
        self._session = aiohttp.ClientSession(timeout=timeout)

    async def close(self):
        if self._session:
            await self._session.close()

    async def _token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._access_token and now < self._token_expires_at:
            return self._access_token
        if not self._session:
            raise RuntimeError("Analyzer is not started")

        url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
        auth = base64.b64encode(
            self.settings.gigachat_auth_key.encode("utf-8")
        ).decode("ascii")
        headers = {
            "Authorization": f"Basic {auth}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        async with self._session.post(
            url, headers=headers, data={"scope": self.settings.gigachat_scope}
        ) as response:
            response.raise_for_status()
            payload = await response.json()
        self._access_token = payload["access_token"]
        self._token_expires_at = now + timedelta(minutes=29)
        return self._access_token

    async def analyze(self, text: str) -> dict:
        if not self._session:
            raise RuntimeError("Analyzer is not started")
        token = await self._token()
        url = f"{self.settings.gigachat_api_base.rstrip('/')}/v1/chat/completions"
        headers = {"Authorization": f"Bearer {token}"}
        body = {
            "model": self.settings.gigachat_model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text[:12000]},
            ],
        }
        async with self._session.post(url, headers=headers, json=body) as response:
            response.raise_for_status()
            payload = await response.json()
        raw = payload["choices"][0]["message"]["content"].strip()
        raw = raw.removeprefix("```json").removesuffix("```").strip()
        result = json.loads(raw)
        result["relevant"] = str(result.get("relevant", "false")).casefold() == "true"
        result["confidence"] = float(result.get("confidence", 0))
        angles = result.get("content_angles", [])
        if isinstance(angles, str):
            angles = [angles]
        result["content_angles"] = [
            str(item).strip()
            for item in angles
            if str(item).strip()
        ][:3]
        for key in ("topic", "pain", "situation", "need"):
            result[key] = str(result.get(key, "")).strip()
        return result

    async def analyze_with_retry(self, text: str, attempts: int = 3) -> dict:
        for attempt in range(attempts):
            try:
                return await self.analyze(text)
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
                if attempt + 1 == attempts:
                    raise
                await asyncio.sleep(2**attempt)
        raise RuntimeError("Unreachable")
