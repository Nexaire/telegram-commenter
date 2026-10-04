from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_api_id: int
    telegram_api_hash: str
    telegram_session: str = "/data/insights.session"
    database_path: str = "/data/insights.db"
    sources_config: str = "/app/config/sources.yaml"
    log_level: str = "INFO"
    dry_run: bool = True
    monitor_lookback_hours: int = Field(default=24, ge=1, le=720)
    monitor_poll_seconds: int = Field(default=300, ge=30, le=3600)
    gigachat_auth_key: str
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat-2"
    gigachat_api_base: str = "https://api.giga.chat"

    def project_config(self) -> dict:
        path = Path(self.sources_config)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not raw.get("target_chat"):
            raise ValueError(f"target_chat is required in {path}")
        raw["sources"] = [
            item for item in raw.get("sources", []) if item.get("enabled", True)
        ]
        if not raw["sources"]:
            raise ValueError(f"At least one enabled source is required in {path}")
        return raw
