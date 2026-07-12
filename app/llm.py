import json
import re

from gigachat import GigaChat


URL_RE = re.compile(r"(?:https?://|www\.|t\.me/|@[A-Za-z0-9_]{4,})", re.I)
AD_RE = re.compile(
    r"\b(?:подпиш(?:итесь|ись)|покупайте|купите|закажите|обращайтесь|пишите нам|"
    r"наш(?:а|и|е)? (?:услуг|продукт|курс)|subscribe|buy now|order now|contact us|our (?:service|product))\b",
    re.I,
)


class CommentGenerator:
    def __init__(self, settings):
        self.settings = settings

    def client(self) -> GigaChat:
        return GigaChat(
            credentials=self.settings.gigachat_credentials,
            scope=self.settings.gigachat_scope,
            model=self.settings.gigachat_model,
            base_url=self.settings.gigachat_base_url,
            verify_ssl_certs=self.settings.gigachat_verify_ssl_certs,
            ca_bundle_file=self.settings.gigachat_ca_bundle_file or None,
            timeout=60,
            max_retries=3,
        )

    async def generate(self, text: str, expertise: str) -> list[str] | None:
        lowered_post = text.casefold()
        if any(topic.casefold() in lowered_post for topic in self.settings.blacklist_topics):
            return None
        black = ", ".join(self.settings.blacklist_topics) or "none"
        brands = ", ".join(self.settings.brand_names) or "none"
        prompt = f"""Analyze the Telegram post below. Return strict JSON only:
{{"skip": false, "reason": "...", "variants": ["comment 1", "comment 2"]}}
Set skip=true for advertising, memes, personal congratulations, conflict, sensitive claims, or these blacklisted topics: {black}.
When not skipped, write exactly two distinct, concise expert comments in the post's language. Each must add a concrete insight, example, caveat, consequence, or thoughtful question. No praise filler, links, @mentions, calls to action, self-promotion, direct advertising, or these brand names: {brands}. Do not claim unverifiable personal experience.
Expertise context: {expertise or 'general business and technology'}
POST:
{text[:12000]}"""
        async with self.client() as client:
            result = await client.achat(prompt)
        raw = result.choices[0].message.content.strip()
        # Модель иногда оборачивает JSON в Markdown, хотя промпт это запрещает.
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
        data = json.loads(raw)
        if data.get("skip"):
            return None
        variants = data.get("variants", [])
        if len(variants) != 2:
            raise ValueError("LLM must return exactly two variants")
        return [self.validate(v) for v in variants]

    def validate(self, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 700:
            raise ValueError("Generated comment is empty or too long")
        if URL_RE.search(value):
            raise ValueError("Generated comment contains a link or mention")
        if AD_RE.search(value):
            raise ValueError("Generated comment contains direct advertising")
        lowered = value.casefold()
        if any(name.casefold() in lowered for name in self.settings.brand_names):
            raise ValueError("Generated comment contains a brand name")
        return value
