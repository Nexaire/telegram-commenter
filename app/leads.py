import re


LEAD_PATTERNS = {
    "цена": (
        re.compile(r"\b(?:сколько|поч[её]м)\b.{0,45}\b(?:стоит|будет|выйдет|обойд[её]тся)\b", re.I),
        re.compile(r"\b(?:цена|стоимость|прайс|бюджет)\b", re.I),
    ),
    "доставка": (
        re.compile(r"\b(?:как|можно ли|реально ли)\b.{0,45}\b(?:привезти|доставить|заказать)\b", re.I),
        re.compile(r"\b(?:доставк\w*|привезти|растамож\w*|таможн\w*)\b", re.I),
    ),
    "покупка": (
        re.compile(r"\b(?:хочу|планирую|думаю)\b.{0,35}\b(?:купить|заказать|взять)\b", re.I),
        re.compile(r"\b(?:где|как)\b.{0,35}\b(?:купить|заказать|оформить)\b", re.I),
        re.compile(r"\bможно\s+(?:купить|заказать|оформить)\b", re.I),
    ),
    "срок": (
        re.compile(r"\bсколько\b.{0,30}\b(?:ждать|везти|ид[её]т)\b", re.I),
        re.compile(r"\b(?:срок|сроки)\b.{0,30}\b(?:доставки|поставки|ожидания)\b", re.I),
    ),
}


def detect_lead(text: str) -> list[str]:
    """Return matched commercial-intent categories without using an external LLM."""
    normalized = " ".join(text.split())
    return [
        category
        for category, patterns in LEAD_PATTERNS.items()
        if any(pattern.search(normalized) for pattern in patterns)
    ]
