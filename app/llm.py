import json
import re

from gigachat import GigaChat


URL_RE = re.compile(r"(?:https?://|www\.|t\.me/|@[A-Za-z0-9_]{4,})", re.I)
AD_RE = re.compile(
    r"\b(?:подпиш(?:итесь|ись)|покупайте|купите|закажите|обращайтесь|пишите нам|"
    r"наш(?:а|и|е)? (?:услуг|продукт|курс)|subscribe|buy now|order now|contact us|our (?:service|product))\b",
    re.I,
)
COMMENT_LABEL_RE = re.compile(
    r"^\s*(?:(?:[-•]\s*)?(?:\*{1,2}|_{1,2})?"
    r"(?:комментарий|вариант|comment|variant)"
    r"(?:\s*(?:№|#|no\.?|n)?\s*\d+)?"
    r"(?:\*{1,2}|_{1,2}|\s*[:.)\]—–-])+\s*)+",
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
{{"skip": false, "reason": "...", "variants": ["...", "..."]}}
Set skip=true for advertising, memes, personal congratulations, conflict, sensitive claims, or these blacklisted topics: {black}.
When not skipped, write exactly two distinct, concise comments in the post's language, as a thoughtful human participant in the discussion rather than an adviser or lecturer. Use a natural observation, restrained emotional reaction, doubt, nuance, consequence, or genuine question. Do not give advice, recommendations, instructions, or tell the author/readers what they should, need to, or ought to do. Avoid imperative verbs. Do not force expertise into every sentence or sound condescending. Return only the comment text inside each JSON string: never prefix it with labels such as "Comment 1", "Комментарий 1", or "Вариант 1". No praise filler, links, @mentions, calls to action, self-promotion, direct advertising, or these brand names: {brands}. Do not claim unverifiable personal experience or invent emotions unsupported by the post.
Expertise context: {expertise or 'general business and technology'}
POST:
{text[:12000]}"""
        async with self.client() as client:
            result = await client.achat(prompt)
            data = self.parse_json(result.choices[0].message.content)
            if data.get("skip"):
                return None
            variants = data.get("variants", [])
            if len(variants) != 2:
                raise ValueError("LLM must return exactly two variants")
            variants = [self.validate(v) for v in variants]

            if getattr(self.settings, "editorial_review", True):
                variants = await self.edit_variants(client, text, variants)

        return variants

    async def edit_variants(self, client: GigaChat, post: str, variants: list[str]) -> list[str]:
        prompt = f"""Ты редактор коротких комментариев для Telegram. Перепиши два черновика так, чтобы они звучали как живые реплики компетентного человека, а не как текст нейросети.

Верни только строгий JSON:
{{"variants": ["...", "..."]}}

Правила:
- сохрани язык, фактический смысл и полезную мысль каждого черновика;
- убери вводную воду, пересказ поста, повторы, канцелярит и чрезмерно гладкие формулировки;
- убери шаблоны вроде «важно отметить», «стоит учитывать», «нельзя не согласиться», «в современном мире», «это подчеркивает», «ключевой аспект», «безусловно»;
- не начинай с похвалы или общего согласия с автором;
- полностью убери советы, рекомендации, инструкции, поучения и обороты «нужно», «следует», «вам стоит», «лучше сделать»; преобразуй их в наблюдение, сомнение, последствие или естественный вопрос;
- используй естественный ритм и простые слова; допустима короткая уместная эмоция — удивление, тревога, ирония, интерес или сомнение, если она следует из поста;
- не добавляй нарочитый сленг, опечатки, эмодзи, пафос или выдуманный личный опыт;
- не добавляй новые факты, ссылки, упоминания, рекламу и призывы к действию;
- не добавляй перед текстом метки «Комментарий 1», «Комментарий 2», «Вариант 1» и подобные;
- каждый вариант должен быть самодостаточным, конкретным и не длиннее исходного;
- варианты должны отличаться по мысли или ракурсу, а не только словами.

Исходный пост (только для контекста):
{post[:12000]}

Черновики:
{json.dumps(variants, ensure_ascii=False)}"""
        result = await client.achat(prompt)
        data = self.parse_json(result.choices[0].message.content)
        edited = data.get("variants", [])
        if len(edited) != 2:
            raise ValueError("Editor must return exactly two variants")
        return [self.validate(v) for v in edited]

    @staticmethod
    def parse_json(raw: str) -> dict:
        raw = raw.strip()
        # Модель иногда оборачивает JSON в Markdown, хотя промпт это запрещает.
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("LLM response must be a JSON object")
        return data

    def validate(self, value: str) -> str:
        value = COMMENT_LABEL_RE.sub("", value).strip()
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
