"""
GitHub Backup - Alert Manager

Coordinates alert sending across multiple channels based on configuration.
"""

import logging
from typing import Optional

from config import Settings
from alerting.base import AlertData, AlertLevel, BaseAlerter
from alerting.email_alerter import EmailAlerter
from alerting.webhook_alerter import WebhookAlerter
from alerting.teams_alerter import TeamsAlerter

logger = logging.getLogger(__name__)


class AlertManager:
    """Manages alert distribution across configured channels."""

    def __init__(self, settings: Settings):
        """Initialize alert manager.

        Args:
            settings: Application settings with alerting configuration.
        """
        self.settings = settings
        self._alerters: dict[str, BaseAlerter] = {}

        # Initialize configured alerters
        if settings.alert_enabled:
            self._init_alerters()

    def _init_alerters(self) -> None:
        """Initialize alerters based on configuration."""
        channels = self.settings.get_alert_channels()

        if "email" in channels:
            missing = self._validate_email_config()
            if missing:
                logger.warning(
                    f"Email alerter disabled - missing required settings: {', '.join(missing)}"
                )
            else:
                self._alerters["email"] = EmailAlerter(self.settings)
                logger.debug("Email alerter initialized")

        if "webhook" in channels:
            missing = self._validate_webhook_config()
            if missing:
                logger.warning(
                    f"Webhook alerter disabled - missing required settings: {', '.join(missing)}"
                )
            else:
                self._alerters["webhook"] = WebhookAlerter(self.settings)
                logger.debug("Webhook alerter initialized")

        if "teams" in channels:
            missing = self._validate_teams_config()
            if missing:
                logger.warning(
                    f"Teams alerter disabled - missing required settings: {', '.join(missing)}"
                )
            else:
                self._alerters["teams"] = TeamsAlerter(self.settings)
                logger.debug("Teams alerter initialized")

    def _validate_email_config(self) -> list[str]:
        """Validate email configuration and return list of missing settings."""
        missing = []
        if not self.settings.smtp_host:
            missing.append("SMTP_HOST")
        if not self.settings.smtp_from:
            missing.append("SMTP_FROM")
        if not self.settings.smtp_to:
            missing.append("SMTP_TO")
        return missing

    def _validate_webhook_config(self) -> list[str]:
        """Validate webhook configuration and return list of missing settings."""
        missing = []
        if not self.settings.webhook_url:
            missing.append("WEBHOOK_URL")
        return missing

    def _validate_teams_config(self) -> list[str]:
        """Validate Teams configuration and return list of missing settings."""
        missing = []
        if not self.settings.teams_webhook_url:
            missing.append("TEAMS_WEBHOOK_URL")
        return missing

    def validate_configuration(self) -> dict[str, list[str]]:
        """Validate all enabled alerter configurations.

        Returns:
            Dictionary mapping channel names to lists of missing settings.
            Empty lists indicate valid configuration.
        """
        results = {}
        channels = self.settings.get_alert_channels()

        if "email" in channels:
            results["email"] = self._validate_email_config()
        if "webhook" in channels:
            results["webhook"] = self._validate_webhook_config()
        if "teams" in channels:
            results["teams"] = self._validate_teams_config()

        return results

    def get_configuration_errors(self) -> list[str]:
        """Get human-readable configuration error messages.

        Returns:
            List of error messages for misconfigured channels.
        """
        errors = []
        validation = self.validate_configuration()

        for channel, missing in validation.items():
            if missing:
                errors.append(
                    f"Alert channel '{channel}' is enabled but missing: {', '.join(missing)}"
                )

        return errors

    def should_send_alert(self, level: AlertLevel) -> bool:
        """Check if an alert should be sent based on configured level.

        Args:
            level: The alert level to check.

        Returns:
            True if alert should be sent.
        """
        if not self.settings.alert_enabled:
            return False

        if not self._alerters:
            return False

        alert_level = self.settings.alert_level

        if alert_level == "all":
            return True
        elif alert_level == "warnings":
            return level in (AlertLevel.WARNING, AlertLevel.ERROR)
        else:  # errors (default)
            return level == AlertLevel.ERROR

    def send_alert(self, alert: AlertData) -> dict[str, bool]:
        """Send alert to all configured channels.

        Args:
            alert: Alert data to send.

        Returns:
            Dictionary mapping channel names to success status.
        """
        results = {}

        if not self.should_send_alert(alert.level):
            logger.debug(f"Alert level {alert.level.value} not configured for sending")
            return results

        for channel, alerter in self._alerters.items():
            try:
                success = alerter.send(alert)
                results[channel] = success
                if success:
                    logger.info(f"Alert sent via {channel}")
                else:
                    logger.warning(f"Failed to send alert via {channel}")
            except Exception as e:
                logger.error(f"Error sending alert via {channel}: {e}")
                results[channel] = False

        return results

    def send_backup_success(
        self,
        backup_id: str,
        stats: dict,
        duration_seconds: float,
        github_owner: Optional[str] = None,
    ) -> dict[str, bool]:
        """Send a backup success alert.

        Args:
            backup_id: Backup identifier.
            stats: Backup statistics dictionary.
            duration_seconds: Backup duration.
            github_owner: GitHub organization/user.

        Returns:
            Dictionary mapping channel names to success status.
        """
        repos_backed_up = stats.get("repos", 0)
        repos_skipped = stats.get("skipped", 0)

        if repos_backed_up == 0 and repos_skipped > 0:
            message = f"All {repos_skipped} repositories are up to date, no backup needed."
            title = "All Repositories Up to Date"
        else:
            message = f"Successfully backed up {repos_backed_up} repositories."
            if repos_skipped > 0:
                message += f" {repos_skipped} unchanged repositories were skipped."
            title = "Backup Completed Successfully"

        alert = AlertData(
            level=AlertLevel.SUCCESS,
            title=title,
            message=message,
            backup_id=backup_id,
            repos_backed_up=repos_backed_up,
            repos_skipped=repos_skipped,
            total_repos=repos_backed_up + repos_skipped,
            issues_count=stats.get("issues", 0),
            prs_count=stats.get("prs", 0),
            releases_count=stats.get("releases", 0),
            wikis_count=stats.get("wikis", 0),
            total_size_bytes=stats.get("total_size", 0),
            duration_seconds=duration_seconds,
            deleted_backups=stats.get("deleted_backups", 0),
            github_owner=github_owner,
        )

        return self.send_alert(alert)

    def send_backup_warning(
        self,
        backup_id: str,
        stats: dict,
        duration_seconds: float,
        warning_messages: list[str],
        github_owner: Optional[str] = None,
    ) -> dict[str, bool]:
        """Send a backup warning alert.

        Args:
            backup_id: Backup identifier.
            stats: Backup statistics dictionary.
            duration_seconds: Backup duration.
            warning_messages: List of warning messages.
            github_owner: GitHub organization/user.

        Returns:
            Dictionary mapping channel names to success status.
        """
        repos_backed_up = stats.get("repos", 0)
        repos_failed = stats.get("errors", 0)

        message = f"Backup completed with {repos_failed} warning(s). "
        message += f"{repos_backed_up} repositories backed up successfully."

        alert = AlertData(
            level=AlertLevel.WARNING,
            title="Backup Completed with Warnings",
            message=message,
            backup_id=backup_id,
            repos_backed_up=repos_backed_up,
            repos_skipped=stats.get("skipped", 0),
            repos_failed=repos_failed,
            total_repos=repos_backed_up + stats.get("skipped", 0) + repos_failed,
            issues_count=stats.get("issues", 0),
            prs_count=stats.get("prs", 0),
            releases_count=stats.get("releases", 0),
            wikis_count=stats.get("wikis", 0),
            total_size_bytes=stats.get("total_size", 0),
            duration_seconds=duration_seconds,
            error_messages=warning_messages,
            deleted_backups=stats.get("deleted_backups", 0),
            github_owner=github_owner,
        )

        return self.send_alert(alert)

    def send_backup_error(
        self,
        backup_id: str,
        error_message: str,
        stats: Optional[dict] = None,
        duration_seconds: float = 0,
        error_messages: Optional[list[str]] = None,
        github_owner: Optional[str] = None,
    ) -> dict[str, bool]:
        """Send a backup error alert.

        Args:
            backup_id: Backup identifier.
            error_message: Main error message.
            stats: Backup statistics dictionary (if available).
            duration_seconds: Backup duration.
            error_messages: List of detailed error messages.
            github_owner: GitHub organization/user.

        Returns:
            Dictionary mapping channel names to success status.
        """
        stats = stats or {}
        errors = error_messages or []
        if error_message and error_message not in errors:
            errors.insert(0, error_message)

        alert = AlertData(
            level=AlertLevel.ERROR,
            title="Backup Failed",
            message=error_message,
            backup_id=backup_id,
            repos_backed_up=stats.get("repos", 0),
            repos_skipped=stats.get("skipped", 0),
            repos_failed=stats.get("errors", 0),
            total_repos=stats.get("repos", 0) + stats.get("skipped", 0) + stats.get("errors", 0),
            issues_count=stats.get("issues", 0),
            prs_count=stats.get("prs", 0),
            releases_count=stats.get("releases", 0),
            wikis_count=stats.get("wikis", 0),
            total_size_bytes=stats.get("total_size", 0),
            duration_seconds=duration_seconds,
            error_messages=errors,
            github_owner=github_owner,
        )

        return self.send_alert(alert)

    def test_connections(self) -> dict[str, bool]:
        """Test all configured alerter connections.

        Returns:
            Dictionary mapping channel names to connection test results.
        """
        results = {}

        for channel, alerter in self._alerters.items():
            try:
                success = alerter.test_connection()
                results[channel] = success
            except Exception as e:
                logger.error(f"Error testing {channel} connection: {e}")
                results[channel] = False

        return results
