from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TrendRelay API"
    environment: str = "development"
    api_host: str = "127.0.0.1"
    api_port: int = 8080
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    database_url: str = "sqlite:///.data/trendrelay.db"
    supabase_url: str = ""
    auth_audience: str = "authenticated"
    device_token_secret: str = ""
    device_token_ttl_hours: int = 8
    publishing_media_roots: str = ".data/downloads,.data/media,.data/productions"
    public_web_url: str = "http://localhost:3000"
    attribution_public_url: str = "http://localhost:8080"
    attribution_hash_secret: SecretStr = SecretStr("")
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from_email: str = ""
    smtp_security: Literal["starttls", "ssl"] = "starttls"
    invitation_delivery_hourly_limit: int = Field(default=20, ge=1, le=1000)
    require_aal2_for_governed_actions: bool = False
    local_auth_bypass: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def publishing_media_root_list(self) -> list[str]:
        return [root.strip() for root in self.publishing_media_roots.split(",") if root.strip()]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
