from pathlib import Path

import yaml
from pydantic import Field
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
    lead_bot_token: str | None = None
    manager_user_ids: str = ""
    report_timezone: str = "Europe/Moscow"
    report_hour: int = Field(default=9, ge=0, le=23)
    report_minute: int = Field(default=0, ge=0, le=59)

    @property
    def manager_ids(self) -> set[int]:
        try:
            result = {
                int(value.strip())
                for value in self.manager_user_ids.split(",")
                if value.strip()
            }
        except ValueError as exc:
            raise ValueError(
                "MANAGER_USER_IDS must contain comma-separated numeric Telegram IDs"
            ) from exc
        if not result:
            raise ValueError("At least one MANAGER_USER_IDS value is required")
        return result

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
