from functools import lru_cache

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

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
