"""
GitHub Backup - Microsoft Teams Alerter

Sends backup alerts to Microsoft Teams via Webhooks using Adaptive Cards.

Compatible with both:
- Microsoft Teams Workflows (recommended, new method)
- Legacy Incoming Webhooks (deprecated, retiring 2026)
"""

import json
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from config import Settings
from alerting.base import AlertData, AlertLevel, BaseAlerter
from ui.console import backup_logger


class TeamsAlerter(BaseAlerter):
    """Sends alerts to Microsoft Teams using Adaptive Cards."""

    def __init__(self, settings: Settings):
        """Initialize Teams alerter.

        Args:
            settings: Application settings with Teams configuration.
        """
        self.settings = settings

    def send(self, alert: AlertData) -> bool:
        """Send alert to Teams.

        Args:
            alert: Alert data to send.

        Returns:
            True if alert was sent successfully.
        """
        if not self.settings.teams_webhook_url:
            backup_logger.warning("No Teams webhook URL configured")
            return False

        try:
            payload = self._build_adaptive_card(alert)
            self._send_to_teams(payload)
            backup_logger.info("Teams alert sent successfully")
            return True

        except Exception as e:
            backup_logger.error(f"Failed to send Teams alert: {e}")
            return False

    def test_connection(self) -> bool:
        """Test Teams webhook connection.

        Returns:
            True if connection is working.
        """
        if not self.settings.teams_webhook_url:
            backup_logger.error("No Teams webhook URL configured")
            return False

        try:
            test_card = self._build_test_card()
            self._send_to_teams(test_card)
            backup_logger.info("Teams connection test successful")
            return True

        except Exception as e:
            backup_logger.error(f"Teams connection test failed: {e}")
            return False

    def _build_adaptive_card(self, alert: AlertData) -> dict[str, Any]:
        """Build Microsoft Teams Adaptive Card payload.

        Args:
            alert: Alert data.

        Returns:
            Adaptive Card payload for Teams webhook.
        """
        # Status styling based on alert level
        status_config = {
            AlertLevel.SUCCESS: {
                "color": "Good",
                "icon": "✓",
                "accent": "28a745",
            },
            AlertLevel.WARNING: {
                "color": "Warning",
                "icon": "⚠",
                "accent": "ffc107",
            },
            AlertLevel.ERROR: {
                "color": "Attention",
                "icon": "✗",
                "accent": "dc3545",
            },
        }
        status = status_config[alert.level]

        # Build facts for the FactSet
        facts = [
            {"title": "Status", "value": f"{status['icon']} {alert.level.value.upper()}"},
            {"title": "Backup ID", "value": alert.backup_id},
            {"title": "Time", "value": alert.timestamp.strftime("%Y-%m-%d %H:%M:%S")},
        ]

        if alert.github_owner:
            facts.append({"title": "GitHub Owner", "value": alert.github_owner})

        facts.extend([
            {"title": "Repos Backed Up", "value": str(alert.repos_backed_up)},
        ])

        if alert.repos_skipped > 0:
            facts.append({"title": "Repos Skipped", "value": f"{alert.repos_skipped} (unchanged)"})

        if alert.repos_failed > 0:
            facts.append({"title": "Repos Failed", "value": str(alert.repos_failed)})

        if alert.repos_backed_up > 0:
            # Always show LFS status for consistency
            lfs_value = str(alert.lfs_repos) if alert.lfs_repos > 0 else "-"
            facts.append({"title": "Repos with LFS", "value": lfs_value})

            facts.extend([
                {"title": "Issues", "value": str(alert.issues_count)},
                {"title": "Pull Requests", "value": str(alert.prs_count)},
                {"title": "Releases", "value": str(alert.releases_count)},
                {"title": "Wikis", "value": str(alert.wikis_count)},
            ])

            if alert.total_size_bytes > 0:
                facts.append({"title": "Total Size", "value": alert.format_size()})

        facts.append({"title": "Duration", "value": alert.format_duration()})

        if alert.deleted_backups > 0:
            facts.append({"title": "Old Backups Removed", "value": str(alert.deleted_backups)})

        # Build card body
        body = [
            {
                "type": "TextBlock",
                "size": "Large",
                "weight": "Bolder",
                "text": f"GitHub Backup: {alert.title}",
                "wrap": True,
                "color": status["color"],
            },
            {
                "type": "TextBlock",
                "text": alert.message,
                "wrap": True,
                "spacing": "Small",
            },
            {
                "type": "FactSet",
                "facts": facts,
                "spacing": "Medium",
            },
        ]

        # Add errors section if any
        if alert.error_messages:
            error_text = "\n".join([f"• {err}" for err in alert.error_messages[:5]])
            if len(alert.error_messages) > 5:
                error_text += f"\n... and {len(alert.error_messages) - 5} more errors"

            body.append({
                "type": "Container",
                "style": "attention",
                "items": [
                    {
                        "type": "TextBlock",
                        "text": "Errors",
                        "weight": "Bolder",
                        "color": "Attention",
                    },
                    {
                        "type": "TextBlock",
                        "text": error_text,
                        "wrap": True,
                        "fontType": "Monospace",
                        "size": "Small",
                    },
                ],
                "spacing": "Medium",
            })

        # Build Adaptive Card payload
        card = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "msteams": {
                            "width": "Full",
                        },
                        "body": body,
                    },
                }
            ],
        }

        return card

    def _build_test_card(self) -> dict[str, Any]:
        """Build a test message Adaptive Card.

        Returns:
            Test Adaptive Card payload.
        """
        from datetime import datetime

        body = [
            {
                "type": "TextBlock",
                "size": "Large",
                "weight": "Bolder",
                "text": "GitHub Backup - Connection Test",
                "wrap": True,
                "color": "Good",
            },
            {
                "type": "TextBlock",
                "text": "This is a test message to verify the Teams webhook configuration.",
                "wrap": True,
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Status", "value": "✓ Connected"},
                    {"title": "Time", "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                ],
            },
        ]

        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "msteams": {
                            "width": "Full",
                        },
                        "body": body,
                    },
                }
            ],
        }

    def _send_to_teams(self, payload: dict) -> None:
        """Send payload to Microsoft Teams webhook.

        Args:
            payload: Adaptive Card payload.

        Raises:
            HTTPError: If the server returns an error status.
            URLError: If there's a connection error.
        """
        json_data = json.dumps(payload, indent=None, ensure_ascii=False)
        data_bytes = json_data.encode("utf-8")

        headers = {
            "Content-Type": "application/json",
        }

        request = Request(
            self.settings.teams_webhook_url,
            data=data_bytes,
            headers=headers,
            method="POST",
        )

        try:
            with urlopen(request, timeout=30) as response:
                response_body = response.read().decode("utf-8")
                backup_logger.debug(f"Teams response: {response.status} - {response_body}")

        except HTTPError as e:
            response_body = ""
            if e.fp:
                try:
                    response_body = e.fp.read().decode("utf-8")[:500]
                except Exception:
                    pass
            backup_logger.error(f"Teams HTTP error {e.code}: {response_body}")
            raise

        except URLError as e:
            backup_logger.error(f"Teams connection error: {e.reason}")
            raise
