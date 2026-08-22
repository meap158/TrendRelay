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
    media_ai_speech_model: str = "base"
    # Auto is capability-tested by the media worker: CUDA when CTranslate2 can
    # actually see it, otherwise an optimized CPU path. Explicit values remain
    # useful for diagnostics and reproducible deployments.
    media_ai_device: Literal["cpu", "cuda", "auto"] = "auto"
    media_ai_compute_type: str = "auto"
    media_ai_cpu_threads: int = Field(default=0, ge=0, le=64)
    media_ai_speech_batch_size: int = Field(default=8, ge=1, le=32)
    media_ai_speech_beam_size: int = Field(default=5, ge=1, le=5)
    media_ai_ocr_interval_seconds: float = 2.0
    media_ai_max_ocr_frames: int = 60
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from_email: str = ""
    smtp_security: Literal["starttls", "ssl"] = "starttls"
    invitation_delivery_hourly_limit: int = Field(default=20, ge=1, le=1000)
    require_aal2_for_governed_actions: bool = False
    local_auth_bypass: bool = True
    publishing_provider: Literal["bundle_social", "zernio", "buffer"] = "bundle_social"
    bundle_social_api_key: str = ""
    bundle_social_team_id: str = ""
    zernio_api_key: str = ""
    buffer_api_key: str = ""
    buffer_organization_id: str = ""
    youtube_data_api_key: SecretStr = SecretStr("")
    #: The MCP server binds this loopback port. Not 8080: that is the API's, and
    #: a contested port besides. Re-declared here so the status surface and the
    #: server agree on where it is.
    mcp_port: int = 8765
    #: Which workspace an MCP caller reaches. Empty resolves to the local
    #: workspace at launch - the one the operator on this machine owns.
    mcp_workspace_id: str = ""
    #: The outward tunnel that lets an assistant reach the MCP server. Read here,
    #: through Settings, so a value in .env is seen the same way `mcp_port` is -
    #: reading os.environ alone would miss it, because .env is not exported there.
    #: Both the id and the key, or neither: half a pair is a misconfiguration.
    control_plane_tunnel_id: str = ""
    control_plane_api_key: str = ""
    #: Optional tunnel knobs. Empty binary means `tunnel-client` on PATH; empty
    #: health port means a free one is chosen.
    tunnel_client_bin: str = ""
    tunnel_log_level: str = "warn"
    tunnel_health_port: str = ""

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


def refresh_settings() -> Settings:
    """Re-read configuration after the local .env file changed.

    A `get_settings` that has no cache is nothing to clear, not an error: tests
    substitute a plain function for it, and this is called from paths - the
    tunnel's doctor, its connection test - whose whole subject is configuration
    somebody has just edited.
    """
    clear = getattr(get_settings, "cache_clear", None)
    if clear is not None:
        clear()
    return get_settings()
