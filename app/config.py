from pathlib import Path

import yaml
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_api_id: int
    telegram_api_hash: str
    telegram_session: str = "./data/commenter.session"
    approval_bot_token: str
    lead_bot_token: str
    approver_user_ids: list[int]
    gigachat_credentials: str
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat-2"
    gigachat_base_url: str = "https://gigachat.devices.sberbank.ru/api/v1"
    gigachat_verify_ssl_certs: bool = False
    gigachat_ca_bundle_file: str | None = None
    editorial_review: bool = True
    database_path: str = "./data/commenter.db"
    channels_config: str = "./config/channels.yaml"
    log_level: str = "INFO"
    dry_run: bool = True
    daily_comment_limit: int = 5
    publish_delay_min_seconds: int = 120
    publish_delay_max_seconds: int = 600
    monitor_lookback_hours: int = 24
    monitor_poll_seconds: int = 300
    published_audit_seconds: int = 900
    daily_report_timezone: str = "Europe/Moscow"
    daily_report_hour: int = 0
    daily_report_minute: int = 5
    lead_monitor_enabled: bool = True
    lead_poll_seconds: int = 3600
    lead_report_hour: int = 9
    lead_report_minute: int = 0
    blacklist_topics: list[str] = []
    brand_names: list[str] = []
    send_as_channel: str | None = None

    @field_validator("approver_user_ids", "blacklist_topics", "brand_names", mode="before")
    @classmethod
    def split_csv(cls, value):
        if isinstance(value, str):
            return [x.strip() for x in value.split(",") if x.strip()]
        return value

    @field_validator("publish_delay_max_seconds")
    @classmethod
    def valid_delay(cls, value, info):
        minimum = info.data.get("publish_delay_min_seconds", 0)
        if value < minimum:
            raise ValueError("PUBLISH_DELAY_MAX_SECONDS must be >= minimum")
        return value

    @field_validator("daily_report_hour", "lead_report_hour")
    @classmethod
    def valid_report_hour(cls, value):
        if not 0 <= value <= 23:
            raise ValueError("Report hour must be between 0 and 23")
        return value

    @field_validator("daily_report_minute", "lead_report_minute")
    @classmethod
    def valid_report_minute(cls, value):
        if not 0 <= value <= 59:
            raise ValueError("Report minute must be between 0 and 59")
        return value

    def channels(self) -> list[dict]:
        raw = yaml.safe_load(Path(self.channels_config).read_text(encoding="utf-8")) or {}
        return [item for item in raw.get("channels", []) if item.get("enabled", True)]
