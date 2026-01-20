"""
GitHub Backup - Configuration Module

Provides type-safe configuration using Pydantic Settings.
All configuration is loaded from environment variables or .env file.
"""

from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # === GitHub Configuration ===
    github_owner: str = Field(
        description="Organization or username to backup"
    )
    github_pat: str = Field(
        default="",
        description="Personal Access Token for GitHub API (optional for public repos only)"
    )
    github_backup_private: bool = Field(
        default=True,
        description="Include private repositories"
    )
    github_backup_forks: bool = Field(
        default=False,
        description="Include forked repositories"
    )
    github_backup_archived: bool = Field(
        default=True,
        description="Include archived repositories"
    )

    # === Backup Configuration ===
    backup_retention_count: int = Field(
        default=7,
        ge=1,
        description="Number of backup copies to retain"
    )
    backup_include_metadata: bool = Field(
        default=True,
        description="Include issues, PRs, and releases"
    )
    backup_include_wiki: bool = Field(
        default=True,
        description="Include wiki repositories"
    )
    backup_incremental: bool = Field(
        default=True,
        description="Only backup repositories that have changed since last backup"
    )

    # === Scheduler Configuration ===
    backup_schedule_enabled: bool = Field(
        default=True,
        description="Enable scheduled backups"
    )
    backup_schedule_mode: Literal["cron", "interval"] = Field(
        default="cron",
        description="Schedule mode: cron (fixed time with day_of_week) or interval (every n hours)"
    )
    backup_schedule_hour: int = Field(
        default=2,
        ge=0,
        le=23,
        description="Hour to run backup (0-23, for daily/weekly mode)"
    )
    backup_schedule_minute: int = Field(
        default=0,
        ge=0,
        le=59,
        description="Minute to run backup (0-59)"
    )
    backup_schedule_day_of_week: str = Field(
        default="*",
        description="Day of week for cron mode (0=Mon, 6=Sun, * for daily, 0,2,4 for Mon/Wed/Fri)"
    )
    backup_schedule_interval_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description="Hours between backups (for interval mode, 1-168)"
    )

    @field_validator("backup_schedule_day_of_week")
    @classmethod
    def validate_day_of_week(cls, v: str) -> str:
        """Validate day_of_week is valid cron format."""
        if v == "*":
            return v
        try:
            days = [int(d.strip()) for d in v.split(",")]
            if not all(0 <= d <= 6 for d in days):
                raise ValueError("Days must be 0-6")
            return v
        except ValueError:
            raise ValueError("day_of_week must be '*' or comma-separated days 0-6 (0=Mon, 6=Sun)")

    # === S3/MinIO Configuration ===
    s3_endpoint_url: Optional[str] = Field(
        default=None,
        description="S3-compatible endpoint URL (None for AWS S3)"
    )
    s3_bucket: str = Field(
        description="Bucket name for backups"
    )
    s3_access_key: str = Field(
        description="S3 access key"
    )
    s3_secret_key: str = Field(
        description="S3 secret key"
    )
    s3_region: str = Field(
        default="us-east-1",
        description="S3 region"
    )
    s3_prefix: str = Field(
        default="",
        description="Optional prefix/folder in S3 bucket (empty = store directly under {owner}/)"
    )
    s3_multipart_threshold: int = Field(
        default=100 * 1024 * 1024,
        description="File size threshold for multipart upload in bytes (default: 100MB)"
    )
    s3_multipart_chunk_size: int = Field(
        default=50 * 1024 * 1024,
        description="Chunk size for multipart upload in bytes (default: 50MB)"
    )

    # === Alerting Configuration ===
    alert_enabled: bool = Field(
        default=False,
        description="Enable alerting system"
    )
    alert_level: Literal["errors", "warnings", "all"] = Field(
        default="errors",
        description="Alert level: errors (only failures), warnings (failures + warnings), all (include success)"
    )
    alert_channels: str = Field(
        default="",
        description="Comma-separated list of active alert channels: email,webhook,teams"
    )

    # SMTP Email Configuration
    smtp_host: Optional[str] = Field(
        default=None,
        description="SMTP server hostname"
    )
    smtp_port: int = Field(
        default=587,
        description="SMTP server port"
    )
    smtp_tls: bool = Field(
        default=True,
        description="Use TLS/STARTTLS for SMTP"
    )
    smtp_ssl: bool = Field(
        default=False,
        description="Use SSL for SMTP (port 465)"
    )
    smtp_user: Optional[str] = Field(
        default=None,
        description="SMTP username"
    )
    smtp_password: Optional[str] = Field(
        default=None,
        description="SMTP password"
    )
    smtp_from: Optional[str] = Field(
        default=None,
        description="Email sender address"
    )
    smtp_from_name: str = Field(
        default="GitHub Backup",
        description="Email sender display name"
    )
    smtp_to: str = Field(
        default="",
        description="Comma-separated list of recipient email addresses"
    )

    # Generic Webhook Configuration
    webhook_url: Optional[str] = Field(
        default=None,
        description="Webhook URL for generic JSON POST alerts"
    )
    webhook_secret: Optional[str] = Field(
        default=None,
        description="Optional secret for webhook HMAC signature (X-Signature header)"
    )

    # Microsoft Teams Configuration
    teams_webhook_url: Optional[str] = Field(
        default=None,
        description="Microsoft Teams Webhook URL"
    )

    @field_validator("alert_channels")
    @classmethod
    def validate_alert_channels(cls, v: str) -> str:
        """Validate alert channels."""
        if not v:
            return v
        valid_channels = {"email", "webhook", "teams"}
        channels = [c.strip().lower() for c in v.split(",") if c.strip()]
        invalid = set(channels) - valid_channels
        if invalid:
            raise ValueError(f"Invalid alert channels: {invalid}. Valid: {valid_channels}")
        return ",".join(channels)

    def get_alert_channels(self) -> list[str]:
        """Get list of active alert channels."""
        if not self.alert_channels:
            return []
        return [c.strip().lower() for c in self.alert_channels.split(",") if c.strip()]

    def get_smtp_recipients(self) -> list[str]:
        """Get list of SMTP recipients."""
        if not self.smtp_to:
            return []
        return [r.strip() for r in self.smtp_to.split(",") if r.strip()]

    @property
    def is_authenticated(self) -> bool:
        """Check if GitHub PAT is configured for authenticated access."""
        return bool(self.github_pat and self.github_pat.strip())

    # === Application Configuration ===
    tz: str = Field(
        default="Etc/UTC",
        description="Timezone for logging"
    )
    log_level: str = Field(
        default="INFO",
        description="Log level (DEBUG, INFO, WARNING, ERROR)"
    )
    data_dir: str = Field(
        default="/data",
        description="Directory for local backup data"
    )
