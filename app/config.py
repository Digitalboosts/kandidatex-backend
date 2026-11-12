"""Environment-driven settings. Variable names match the previous Next.js backend."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # "production" turns on secure cookies and hides nothing else; see below.
    environment: Literal["development", "test", "production"] = "development"

    database_url: str
    jwt_secret: Annotated[str, Field(min_length=32)]

    livekit_api_key: Annotated[str, Field(min_length=1)]
    livekit_api_secret: Annotated[str, Field(min_length=32)]
    # Address this backend uses to reach the LiveKit API.
    livekit_host: str
    # Address phones and browsers use to connect to LiveKit.
    livekit_public_url: str
    livekit_token_ttl_seconds: Annotated[int, Field(ge=60, le=3600)] = 600

    # Readiness score (0-100) a candidate must reach to finish calibration.
    readiness_threshold: Annotated[int, Field(ge=1, le=100)] = 80
    # Optional. Registering with this code creates an ADMIN. Empty disables it.
    admin_registration_code: str | None = None

    # Comma-separated browser origins allowed to call the API with credentials,
    # e.g. "https://dashboard.example.com,http://localhost:3000". Empty = no CORS
    # (use this when the dashboard proxies /api/* so everything is same-origin).
    cors_origins: str = ""

    # Session cookie. For a dashboard on a different site than the API use
    # COOKIE_SAMESITE=none (this forces Secure on). Same-site: keep "lax".
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_secure: bool | None = None  # default: true in production
    cookie_domain: str | None = None

    # Interactive docs at /docs and /redoc, schema at /openapi.json.
    docs_enabled: bool = True

    @model_validator(mode="after")
    def _normalise(self) -> "Settings":
        if self.admin_registration_code == "":
            self.admin_registration_code = None
        if self.cookie_domain == "":
            self.cookie_domain = None
        if self.cookie_secure is None:
            self.cookie_secure = self.environment == "production"
        if self.cookie_samesite == "none":
            self.cookie_secure = True  # browsers reject SameSite=None without Secure
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
