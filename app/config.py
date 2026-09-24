from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_api_id: int
    telegram_api_hash: str
    telegram_session: str = "./data/monitor.session"
    database_path: str = "./data/monitor.db"
    channels_config: str = "./config/channels.yaml"
    log_level: str = "INFO"
    dry_run: bool = True
    monitor_lookback_hours: int = 24
    monitor_poll_seconds: int = 300

    def monitor_config(self) -> dict:
        path = Path(self.channels_config)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not raw.get("target_channel"):
            raise ValueError(f"target_channel is required in {path}")
        raw["sources"] = [
            item for item in raw.get("sources", []) if item.get("enabled", True)
        ]
        if not raw["sources"]:
            raise ValueError(f"At least one enabled source is required in {path}")
        return raw
