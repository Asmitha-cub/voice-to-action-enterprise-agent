from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    # --- Application ---
    app_name: str = 'Voice-to-Action Enterprise Agent'
    environment: str = 'development'
    app_api_key: str | None = None
    auth_enabled: bool = False
    allowed_origins: str = 'http://localhost:5173,http://127.0.0.1:5173'

    # --- LLM + Speech (OpenAI-compatible, e.g. Groq) ---
    groq_api_key: str | None = None
    groq_base_url: str = 'https://api.groq.com/openai/v1'
    llm_model: str = 'openai/gpt-oss-20b'
    whisper_model: str = 'whisper-large-v3-turbo'
    max_audio_mb: int = 25
    llm_timeout_seconds: float = 30
    tool_timeout_seconds: float = 20
    max_retries: int = 2

    # --- Database ---
    database_url: str = 'postgresql+psycopg://enterprise:enterprise@localhost:5432/enterprise_agent'
    seed_on_startup: bool = True

    # --- Vector store ---
    qdrant_url: str = 'http://localhost:6333'
    qdrant_api_key: str | None = None
    qdrant_collection: str = 'enterprise_knowledge'

    # --- Email notifications (task assignment) ---
    email_enabled: bool = True
    smtp_host: str | None = None
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = False
    smtp_from: str = 'Enterprise Agent <no-reply@enterprise-agent.local>'
    task_notification_email: str | None = None
    smtp_timeout_seconds: float = 15

    # --- Multi-channel delivery (pluggable notification channels) ---
    # Which channels a delivery uses by default when none are specified.
    default_channels: str = 'email'
    # Shared HTTP timeout for channels that call an external API (WhatsApp/Teams/Slack).
    http_timeout_seconds: float = 15

    # WhatsApp via Twilio (https://www.twilio.com/whatsapp)
    whatsapp_enabled: bool = True
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_whatsapp_from: str | None = None  # e.g. 'whatsapp:+14155238886'

    # Microsoft Teams via an Incoming Webhook URL (per team channel)
    teams_enabled: bool = True
    teams_webhook_url: str | None = None

    # Slack via an Incoming Webhook URL (shows the framework is open-ended)
    slack_enabled: bool = True
    slack_webhook_url: str | None = None

    # --- Gmail address lookup (find an unregistered person's email in your mail) ---
    # Off by default. When on, an unknown name is looked up via IMAP in the same
    # Gmail account used for SMTP (reuses smtp_username / smtp_password).
    gmail_lookup_enabled: bool = False
    imap_host: str = 'imap.gmail.com'
    imap_port: int = 993
    imap_timeout_seconds: float = 12

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(',') if origin.strip()]

    @property
    def default_channels_list(self) -> list[str]:
        return [c.strip().lower() for c in (self.default_channels or '').split(',') if c.strip()] or ['email']

    @property
    def smtp_configured(self) -> bool:
        return bool(self.email_enabled and self.smtp_host
                    and (not self.smtp_username or self.smtp_password))


@lru_cache
def settings() -> Settings:
    return Settings()
