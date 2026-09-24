import re
from dataclasses import dataclass

SPACE_RE = re.compile(r"\s+")
DASH_RE = re.compile(r"[‐‑‒–—−]")


def normalize(text: str) -> str:
    """Normalize Telegram text for case-insensitive phrase matching."""
    text = DASH_RE.sub("-", text.casefold().replace("ё", "е"))
    return SPACE_RE.sub(" ", text).strip()


@dataclass(frozen=True)
class MatchResult:
    keywords: tuple[str, ...]
    intents: tuple[str, ...]


class CoverRequestDetector:
    def __init__(
        self,
        keywords: list[str],
        intent_phrases: list[str] | None = None,
        exclude_phrases: list[str] | None = None,
        require_intent: bool = False,
    ):
        self.keywords = tuple(normalize(value) for value in keywords if value.strip())
        self.intents = tuple(normalize(value) for value in (intent_phrases or []) if value.strip())
        self.excludes = tuple(normalize(value) for value in (exclude_phrases or []) if value.strip())
        self.require_intent = require_intent
        if not self.keywords:
            raise ValueError("matching.keywords must contain at least one phrase")
        if require_intent and not self.intents:
            raise ValueError("matching.intent_phrases are required when require_intent=true")

    def detect(self, text: str) -> MatchResult | None:
        normalized = normalize(text)
        if not normalized or any(phrase in normalized for phrase in self.excludes):
            return None
        keywords = tuple(phrase for phrase in self.keywords if phrase in normalized)
        if not keywords:
            return None
        intents = tuple(phrase for phrase in self.intents if phrase in normalized)
        if self.require_intent and not intents:
            return None
        return MatchResult(keywords=keywords, intents=intents)
