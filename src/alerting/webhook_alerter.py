"""
GitHub Backup - Generic Webhook Alerter

Sends backup alerts via HTTP POST with JSON payload.
Supports optional HMAC signature for verification.
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime
from typing import Any, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from config import Settings
from alerting.base import AlertData, AlertLevel, BaseAlerter

logger = logging.getLogger(__name__)


class WebhookAlerter(BaseAlerter):
    """Sends alerts via generic HTTP webhook with JSON payload."""

    def __init__(self, settings: Settings):
        """Initialize webhook alerter.

        Args:
            settings: Application settings with webhook configuration.
        """
        self.settings = settings

    def send(self, alert: AlertData) -> bool:
        """Send alert via webhook.

        Args:
            alert: Alert data to send.

        Returns:
            True if webhook was called successfully.
        """
        if not self.settings.webhook_url:
            logger.warning("No webhook URL configured")
            return False

        try:
            payload = self._build_payload(alert)
            self._send_webhook(payload)
            logger.info("Webhook alert sent successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to send webhook alert: {e}")
            return False

    def test_connection(self) -> bool:
        """Test webhook connection by sending a test payload.

        Returns:
            True if connection is working.
        """
        if not self.settings.webhook_url:
            logger.error("No webhook URL configured")
            return False

        try:
            test_payload = {
                "event": "test",
                "service": "github-backup",
                "timestamp": datetime.now().isoformat(),
                "message": "Webhook connection test",
            }
            self._send_webhook(test_payload)
            logger.info("Webhook connection test successful")
            return True

        except Exception as e:
            logger.error(f"Webhook connection test failed: {e}")
            return False

    def _build_payload(self, alert: AlertData) -> dict[str, Any]:
        """Build JSON payload for webhook.

        The payload structure is designed to be compatible with common
        webhook receivers and easy to parse.

        Args:
            alert: Alert data.

        Returns:
            Dictionary payload for JSON serialization.
        """
        return {
            "event": "backup_status",
            "service": "github-backup",
            "timestamp": alert.timestamp.isoformat(),
            "level": alert.level.value,
            "level_color": alert.level.color_hex,
            "title": alert.title,
            "message": alert.message,
            "backup_id": alert.backup_id,
            "github_owner": alert.github_owner,
            "stats": {
                "repos_backed_up": alert.repos_backed_up,
                "repos_skipped": alert.repos_skipped,
                "repos_failed": alert.repos_failed,
                "total_repos": alert.total_repos,
                "issues": alert.issues_count,
                "pull_requests": alert.prs_count,
                "releases": alert.releases_count,
                "wikis": alert.wikis_count,
                "total_size_bytes": alert.total_size_bytes,
                "total_size_formatted": alert.format_size(),
                "duration_seconds": alert.duration_seconds,
                "duration_formatted": alert.format_duration(),
                "deleted_backups": alert.deleted_backups,
            },
            "errors": alert.error_messages,
            "is_success": alert.level == AlertLevel.SUCCESS,
            "is_warning": alert.level == AlertLevel.WARNING,
            "is_error": alert.level == AlertLevel.ERROR,
        }

    def _send_webhook(self, payload: dict) -> None:
        """Send payload to webhook URL.

        Args:
            payload: Dictionary to send as JSON.

        Raises:
            HTTPError: If the server returns an error status.
            URLError: If there's a connection error.
        """
        json_data = json.dumps(payload, indent=None, ensure_ascii=False)
        data_bytes = json_data.encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "GitHubBackup/1.0",
        }

        # Add HMAC signature if secret is configured
        if self.settings.webhook_secret:
            signature = self._compute_signature(data_bytes)
            headers["X-Signature"] = signature
            headers["X-Signature-256"] = f"sha256={signature}"

        request = Request(
            self.settings.webhook_url,
            data=data_bytes,
            headers=headers,
            method="POST",
        )

        try:
            with urlopen(request, timeout=30) as response:
                status = response.status
                if status >= 400:
                    raise HTTPError(
                        self.settings.webhook_url,
                        status,
                        f"HTTP {status}",
                        response.headers,
                        None,
                    )
                logger.debug(f"Webhook response: {status}")

        except HTTPError as e:
            response_body = ""
            if e.fp:
                try:
                    response_body = e.fp.read().decode("utf-8")[:500]
                except Exception:
                    pass
            logger.error(f"Webhook HTTP error {e.code}: {response_body}")
            raise

        except URLError as e:
            logger.error(f"Webhook connection error: {e.reason}")
            raise

    def _compute_signature(self, data: bytes) -> str:
        """Compute HMAC-SHA256 signature for payload.

        Args:
            data: Raw payload bytes.

        Returns:
            Hex-encoded HMAC-SHA256 signature.
        """
        secret = self.settings.webhook_secret.encode("utf-8")
        signature = hmac.new(secret, data, hashlib.sha256)
        return signature.hexdigest()
