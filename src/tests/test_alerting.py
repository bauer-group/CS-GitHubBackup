"""
GitHub Backup - Alerting Tests

Tests for the alerting system including webhooks and Teams integration.
"""

import hashlib
import hmac
import json
from datetime import datetime

import pytest
import responses

from config import Settings
from alerting.base import AlertData, AlertLevel
from alerting.manager import AlertManager
from alerting.webhook_alerter import WebhookAlerter
from alerting.teams_alerter import TeamsAlerter


class TestAlertLevel:
    """Tests for AlertLevel enum."""

    def test_color_hex(self):
        """Test alert level color codes."""
        assert AlertLevel.SUCCESS.color_hex == "28a745"
        assert AlertLevel.WARNING.color_hex == "ffc107"
        assert AlertLevel.ERROR.color_hex == "dc3545"

    def test_color_name(self):
        """Test alert level color names."""
        assert AlertLevel.SUCCESS.color_name == "green"
        assert AlertLevel.WARNING.color_name == "yellow"
        assert AlertLevel.ERROR.color_name == "red"

    def test_emoji(self):
        """Test alert level emojis."""
        assert AlertLevel.SUCCESS.emoji == "✓"
        assert AlertLevel.WARNING.emoji == "⚠"
        assert AlertLevel.ERROR.emoji == "✗"


class TestAlertData:
    """Tests for AlertData dataclass."""

    def test_format_size(self):
        """Test size formatting."""
        alert = AlertData(
            level=AlertLevel.SUCCESS,
            title="Test",
            message="Test message",
            backup_id="2024-01-15_02-00-00",
            total_size_bytes=1024,
        )
        assert alert.format_size() == "1.0 KB"

        alert.total_size_bytes = 1024 * 1024 * 2.5
        assert alert.format_size() == "2.5 MB"

        alert.total_size_bytes = 1024 * 1024 * 1024 * 1.5
        assert alert.format_size() == "1.5 GB"

    def test_format_duration(self):
        """Test duration formatting."""
        alert = AlertData(
            level=AlertLevel.SUCCESS,
            title="Test",
            message="Test message",
            backup_id="2024-01-15_02-00-00",
            duration_seconds=45.5,
        )
        assert alert.format_duration() == "45.5s"

        alert.duration_seconds = 125
        assert alert.format_duration() == "2m 5s"

        alert.duration_seconds = 3725
        assert alert.format_duration() == "1h 2m"


class TestAlertManager:
    """Tests for AlertManager."""

    def test_disabled_alerting(self, test_settings: Settings):
        """Test that disabled alerting doesn't send alerts."""
        test_settings.alert_enabled = False
        manager = AlertManager(test_settings)

        assert manager.should_send_alert(AlertLevel.ERROR) is False
        assert manager.should_send_alert(AlertLevel.SUCCESS) is False

    def test_alert_level_filtering_errors(self, alert_settings: Settings):
        """Test that errors level only sends on errors."""
        alert_settings.alert_level = "errors"
        manager = AlertManager(alert_settings)

        assert manager.should_send_alert(AlertLevel.ERROR) is True
        assert manager.should_send_alert(AlertLevel.WARNING) is False
        assert manager.should_send_alert(AlertLevel.SUCCESS) is False

    def test_alert_level_filtering_warnings(self, alert_settings: Settings):
        """Test that warnings level sends on errors and warnings."""
        alert_settings.alert_level = "warnings"
        manager = AlertManager(alert_settings)

        assert manager.should_send_alert(AlertLevel.ERROR) is True
        assert manager.should_send_alert(AlertLevel.WARNING) is True
        assert manager.should_send_alert(AlertLevel.SUCCESS) is False

    def test_alert_level_filtering_all(self, alert_settings: Settings):
        """Test that all level sends on everything."""
        alert_settings.alert_level = "all"
        manager = AlertManager(alert_settings)

        assert manager.should_send_alert(AlertLevel.ERROR) is True
        assert manager.should_send_alert(AlertLevel.WARNING) is True
        assert manager.should_send_alert(AlertLevel.SUCCESS) is True

    def test_configuration_validation_missing_smtp(self, temp_dir):
        """Test that missing SMTP config is detected."""
        settings = Settings(
            github_owner="test",
            github_pat="test",
            s3_endpoint_url="http://test",
            s3_bucket="test",
            s3_access_key="test",
            s3_secret_key="test",
            alert_enabled=True,
            alert_channels="email",
            smtp_host="",  # Missing
            smtp_from="",  # Missing
            smtp_to="",    # Missing
            data_dir=str(temp_dir),
        )
        manager = AlertManager(settings)
        errors = manager.get_configuration_errors()

        assert len(errors) == 1
        assert "email" in errors[0]
        assert "SMTP_HOST" in errors[0]
        assert "SMTP_FROM" in errors[0]
        assert "SMTP_TO" in errors[0]

    def test_configuration_validation_missing_webhook(self, temp_dir):
        """Test that missing webhook URL is detected."""
        settings = Settings(
            github_owner="test",
            github_pat="test",
            s3_endpoint_url="http://test",
            s3_bucket="test",
            s3_access_key="test",
            s3_secret_key="test",
            alert_enabled=True,
            alert_channels="webhook",
            webhook_url="",  # Missing
            data_dir=str(temp_dir),
        )
        manager = AlertManager(settings)
        errors = manager.get_configuration_errors()

        assert len(errors) == 1
        assert "webhook" in errors[0]
        assert "WEBHOOK_URL" in errors[0]

    def test_configuration_validation_missing_teams(self, temp_dir):
        """Test that missing Teams URL is detected."""
        settings = Settings(
            github_owner="test",
            github_pat="test",
            s3_endpoint_url="http://test",
            s3_bucket="test",
            s3_access_key="test",
            s3_secret_key="test",
            alert_enabled=True,
            alert_channels="teams",
            teams_webhook_url="",  # Missing
            data_dir=str(temp_dir),
        )
        manager = AlertManager(settings)
        errors = manager.get_configuration_errors()

        assert len(errors) == 1
        assert "teams" in errors[0]
        assert "TEAMS_WEBHOOK_URL" in errors[0]


class TestWebhookAlerter:
    """Tests for WebhookAlerter."""

    @responses.activate
    def test_send_alert(self, alert_settings: Settings):
        """Test sending an alert via webhook."""
        responses.add(
            responses.POST,
            "https://webhook.example.com/test",
            json={"status": "ok"},
            status=200,
        )

        alerter = WebhookAlerter(alert_settings)
        alert = AlertData(
            level=AlertLevel.SUCCESS,
            title="Backup Completed",
            message="Successfully backed up 10 repos",
            backup_id="2024-01-15_02-00-00",
            repos_backed_up=10,
            duration_seconds=300,
        )

        result = alerter.send(alert)

        assert result is True
        assert len(responses.calls) == 1

        # Verify payload
        request_body = json.loads(responses.calls[0].request.body)
        assert request_body["event"] == "backup_alert"
        assert request_body["level"] == "success"
        assert request_body["title"] == "Backup Completed"
        assert request_body["stats"]["repos_backed_up"] == 10

    @responses.activate
    def test_send_alert_with_signature(self, alert_settings: Settings):
        """Test that webhook signature is computed correctly."""
        responses.add(
            responses.POST,
            "https://webhook.example.com/test",
            json={"status": "ok"},
            status=200,
        )

        alerter = WebhookAlerter(alert_settings)
        alert = AlertData(
            level=AlertLevel.ERROR,
            title="Backup Failed",
            message="Error occurred",
            backup_id="2024-01-15_02-00-00",
        )

        alerter.send(alert)

        # Verify signature headers
        request = responses.calls[0].request
        assert "X-Signature" in request.headers
        assert "X-Signature-256" in request.headers

        # Verify signature is correct
        expected_sig = hmac.new(
            b"test-secret",
            request.body,
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["X-Signature"] == expected_sig

    @responses.activate
    def test_send_alert_handles_error(self, alert_settings: Settings):
        """Test handling of webhook errors."""
        responses.add(
            responses.POST,
            "https://webhook.example.com/test",
            json={"error": "Server Error"},
            status=500,
        )

        alerter = WebhookAlerter(alert_settings)
        alert = AlertData(
            level=AlertLevel.ERROR,
            title="Test",
            message="Test",
            backup_id="test",
        )

        result = alerter.send(alert)
        assert result is False


class TestTeamsAlerter:
    """Tests for TeamsAlerter."""

    @responses.activate
    def test_send_alert(self, alert_settings: Settings):
        """Test sending an alert to Teams."""
        responses.add(
            responses.POST,
            "https://teams.webhook.office.com/test",
            body="1",
            status=200,
        )

        alerter = TeamsAlerter(alert_settings)
        alert = AlertData(
            level=AlertLevel.SUCCESS,
            title="Backup Completed",
            message="Successfully backed up 10 repos",
            backup_id="2024-01-15_02-00-00",
            repos_backed_up=10,
            repos_skipped=5,
            duration_seconds=300,
            github_owner="test-org",
        )

        result = alerter.send(alert)

        assert result is True
        assert len(responses.calls) == 1

        # Verify payload is Adaptive Card format
        request_body = json.loads(responses.calls[0].request.body)
        assert request_body["type"] == "message"
        assert len(request_body["attachments"]) == 1
        assert request_body["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"

    @responses.activate
    def test_send_error_alert_with_errors(self, alert_settings: Settings):
        """Test error alert includes error list."""
        responses.add(
            responses.POST,
            "https://teams.webhook.office.com/test",
            body="1",
            status=200,
        )

        alerter = TeamsAlerter(alert_settings)
        alert = AlertData(
            level=AlertLevel.ERROR,
            title="Backup Failed",
            message="Multiple errors occurred",
            backup_id="2024-01-15_02-00-00",
            error_messages=["Error 1: Connection failed", "Error 2: Timeout"],
        )

        result = alerter.send(alert)

        assert result is True

        # Verify errors are included in card
        request_body = json.loads(responses.calls[0].request.body)
        card_body = request_body["attachments"][0]["content"]["body"]

        # Find the container with errors
        error_container = None
        for item in card_body:
            if item.get("type") == "Container" and item.get("style") == "attention":
                error_container = item
                break

        assert error_container is not None

    @responses.activate
    def test_test_connection(self, alert_settings: Settings):
        """Test Teams connection test."""
        responses.add(
            responses.POST,
            "https://teams.webhook.office.com/test",
            body="1",
            status=200,
        )

        alerter = TeamsAlerter(alert_settings)
        result = alerter.test_connection()

        assert result is True

    @responses.activate
    def test_handles_teams_error(self, alert_settings: Settings):
        """Test handling of Teams API errors."""
        responses.add(
            responses.POST,
            "https://teams.webhook.office.com/test",
            json={"error": "Bad Request"},
            status=400,
        )

        alerter = TeamsAlerter(alert_settings)
        alert = AlertData(
            level=AlertLevel.ERROR,
            title="Test",
            message="Test",
            backup_id="test",
        )

        result = alerter.send(alert)
        assert result is False
