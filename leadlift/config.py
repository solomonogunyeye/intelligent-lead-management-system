"""LEADLIFT — Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_path: str = Field(
        default="leadlift/data/leadlift.db",
        description="Path to SQLite database file",
    )

    # OCR
    tesseract_cmd: str = Field(
        default="",
        description="Full path to tesseract.exe if not on PATH",
    )

    # AI provider
    ollama_host: str = Field(
        default="http://127.0.0.1:11434",
        description="Ollama HTTP API endpoint",
    )
    ollama_model: str = Field(
        default="llama3.2:latest",
        description="Ollama model to use for AI processing",
    )
    ollama_timeout: int = Field(
        default=30,
        description="Ollama request timeout in seconds",
    )

    # Business configuration
    business_services: List[str] = Field(
        default=["Web Development", "Digital Marketing", "SEO", "Content Creation", "Maintenance"],
        description="Comma-separated configured business services",
    )
    stale_hours: int = Field(
        default=48,
        description="Hours beyond which an unanswered inquiry is considered stale",
    )
    communication_tone: str = Field(
        default="Professional",
        description="Default communication tone for response drafting",
    )

    # Gmail intake (Phase 3B)
    gmail_oauth_client_id: str = Field(
        default="",
        description="Google OAuth client ID for Gmail API (from GCP console)",
    )
    gmail_oauth_client_secret: str = Field(
        default="",
        description="Google OAuth client secret for Gmail API (from GCP console)",
    )
    gmail_oauth_refresh_token: str = Field(
        default="",
        description="Google OAuth refresh token for Gmail API (from OAuth flow)",
    )
    gmail_oauth_token_encryption_key: str = Field(
        default="",
        description="Encryption key for token storage at rest (optional; uses keyring when set)",
    )
    gmail_credentials_encryption_key: str = Field(
        default="",
        description="Encryption key for OAuth credentials at rest (optional; uses keyring when set)",
    )
    gmail_max_retrieved_messages: int = Field(
        default=100,
        description="Maximum number of Gmail messages to retrieve per scan",
    )
    gmail_max_message_age_days: int = Field(
        default=30,
        description="Maximum age of messages to retrieve per scan (in days)",
    )
    gmail_use_html_fallback: bool = Field(
        default=True,
        description="Use HTML to text conversion for messages with only HTML bodies",
    )
    gmail_scan_interval_seconds: int = Field(
        default=0,
        description="Scan interval in seconds (0 = disabled, no background scanning)",
    )
    gmail_api_base_url: str = Field(
        default="https://gmail.googleapis.com/gmail/v1",
        description="Gmail API base URL",
    )
    gmail_api_timeout_seconds: int = Field(
        default=30,
        description="Gmail API request timeout in seconds",
    )

    # WhatsApp Business intake (Phase 3C)
    whatsapp_app_id: str = Field(
        default="",
        description="WhatsApp Business Platform app ID (from Meta developer console)",
    )
    whatsapp_app_secret: str = Field(
        default="",
        description="WhatsApp Business Platform app secret (for webhook signature verification)",
    )
    whatsapp_verify_token: str = Field(
        default="",
        description="WhatsApp webhook verification token (set during webhook setup)",
    )
    whatsapp_business_account_id: str = Field(
        default="",
        description="WhatsApp Business Account ID",
    )
    whatsapp_ph_number_id: str = Field(
        default="",
        description="WhatsApp Phone Number ID",
    )
    whatsapp_webhook_url: str = Field(
        default="",
        description="WhatsApp webhook endpoint URL",
    )
    whatsapp_mode: str = Field(
        default="webhook",
        description="WhatsApp webhook mode (webhook or polling)",
    )
    whatsapp_verify_token_check: str = Field(
        default="",
        description="Configured verify token for security validation",
    )

    # Instagram Professional intake (Phase 3D)
    instagram_app_id: str = Field(
        default="",
        description="Instagram/Meta app ID (from Meta developer console)",
    )
    instagram_app_secret: str = Field(
        default="",
        description="Instagram/Meta app secret (for webhook signature verification)",
    )
    instagram_verify_token: str = Field(
        default="",
        description="Instagram webhook verification token (set during webhook setup)",
    )
    instagram_access_token: str = Field(
        default="",
        description="Instagram long-lived access token (for profile lookups, optional)",
    )
    instagram_business_account_id: str = Field(
        default="",
        description="Instagram Professional account ID",
    )
    instagram_page_id: str = Field(
        default="",
        description="Facebook Page ID linked to Instagram Professional account",
    )

    # Facebook Messenger intake (Phase 3E)
    facebook_app_id: str = Field(
        default="",
        description="Facebook/Meta app ID (from Meta developer console)",
    )
    facebook_app_secret: str = Field(
        default="",
        description="Facebook/Meta app secret (for webhook signature verification)",
    )
    facebook_verify_token: str = Field(
        default="",
        description="Facebook webhook verification token (set during webhook setup)",
    )
    facebook_access_token: str = Field(
        default="",
        description="Facebook Page access token (optional)",
    )
    facebook_page_id: str = Field(
        default="",
        description="Facebook Page ID for Messenger",
    )

    # Telegram Bot intake (Phase 3F)
    telegram_bot_token: str = Field(
        default="",
        description="Telegram bot token (from BotFather)",
    )
    telegram_webhook_secret: str = Field(
        default="",
        description="Telegram webhook secret token (set during webhook setup)",
    )
    telegram_bot_username: str = Field(
        default="",
        description="Telegram bot username",
    )

    # Website contact form intake (Phase 3G)
    website_form_webhook_secret: str = Field(
        default="",
        description="Shared secret for website contact-form webhook authentication",
    )
    website_form_max_payload_bytes: int = Field(
        default=65536,
        description="Maximum allowed payload size in bytes (default 64KB)",
    )
    website_form_max_field_length: int = Field(
        default=10000,
        description="Maximum allowed length for individual text fields",
    )

    # Backup (Phase 4A.2)
    backup_dir: str = Field(
        default="leadlift/data/backups",
        description="Directory for database backups",
    )
    backup_retention: int = Field(
        default=10,
        description="Number of successful backups to retain",
    )

    # Logging (Phase 4B)
    log_dir: str = Field(
        default="leadlift/data/logs",
        description="Directory for application log files",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    log_max_bytes: int = Field(
        default=5 * 1024 * 1024,
        description="Maximum size per log file before rotation (bytes)",
    )
    log_backup_count: int = Field(
        default=5,
        description="Number of rotated log files to retain",
    )

    # Derived
    @field_validator("business_services", mode="before")
    @classmethod
    def _parse_services(cls, v):
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v or []

    @property
    def db_path_obj(self) -> Path:
        """Return database path as a Path object."""
        return Path(self.database_path)

    @property
    def db_dir(self) -> Path:
        """Return the directory containing the database, creating if needed."""
        return self.db_path_obj.parent

    def ensure_db_dir(self) -> None:
        """Ensure the database directory exists."""
        self.db_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    """Load and validate application settings, ensuring the DB directory exists."""
    settings = Settings()
    settings.ensure_db_dir()
    return settings


# Module-level singleton (thread-safe lazy initialization)
_settings: Settings | None = None
_settings_lock = threading.Lock()


def get_settings() -> Settings:
    """Get the application settings singleton (thread-safe)."""
    global _settings
    if _settings is None:
        with _settings_lock:
            if _settings is None:
                _settings = load_settings()
    return _settings