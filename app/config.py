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

    # Vision model for the screen detection agent (Azure OpenAI v1 / OpenAI-compatible).
    openai_six_luna_api_key: str | None = None
    openai_six_luna_base_url: str | None = None
    openai_six_luna_model: str | None = None
    screen_agent_timeout_seconds: Annotated[float, Field(gt=0, le=120)] = 30
    # Extra applications that always raise a HIGH alert, comma-separated (on top of ChatGPT, Claude, Gemini, Copilot).
    screen_agent_high_apps: str = ""

    # Azure Blob Storage for interview recordings. Container names must be lowercase letters,
    # digits and hyphens (3-63 chars). The SAS token needs read/write/create/list/delete on blobs.
    azure_storage_endpoint: str | None = None
    azure_storage_sas_token: str | None = None
    azure_video_container: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")] = "interview-videos"
    # Screenshots of HIGH screen alerts (same storage account, separate container).
    azure_image_container: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")] = "interviews-images"

    # Razorpay (one-time payment for the plan).
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None
    razorpay_webhook_secret: str | None = None
    # When true, creating an interview needs an active subscription with interviews left (admins are exempt).
    subscription_enforced: bool = True

    # Outgoing email (Google Workspace SMTP by default). EMAIL_PASSWORD_C is an app password.
    email_sender_c: str | None = None
    email_password_c: str | None = None
    email_smtp_host: str = "smtp.gmail.com"
    email_smtp_port: int = 587
    email_sender_name: str = "Kandidatex"
    # Link used by the buttons in the emails.
    app_url: str = "https://www.kandidatex.com/dashboard"

    # Interactive docs at /docs and /redoc, schema at /openapi.json.
    docs_enabled: bool = True

    @model_validator(mode="after")
    def _normalise(self) -> "Settings":
        if self.admin_registration_code == "":
            self.admin_registration_code = None
        for name in ("razorpay_key_id", "razorpay_key_secret", "razorpay_webhook_secret"):
            if getattr(self, name) == "":
                setattr(self, name, None)
        if self.cookie_domain == "":
            self.cookie_domain = None
        if self.cookie_secure is None:
            self.cookie_secure = self.environment == "production"
        if self.cookie_samesite == "none":
            self.cookie_secure = True  # browsers reject SameSite=None without Secure
        return self

    @property
    def screen_agent_high_app_list(self) -> list[str]:
        return [name.strip() for name in self.screen_agent_high_apps.split(",") if name.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
