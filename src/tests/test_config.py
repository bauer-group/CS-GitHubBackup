"""
GitHub Backup - Configuration Tests

Tests for configuration validation and parsing.
"""

import pytest
from pydantic import ValidationError

from config import Settings


class TestSettings:
    """Tests for Settings class."""

    def test_valid_settings(self, temp_dir):
        """Test creating settings with valid values."""
        settings = Settings(
            github_owner="test-org",
            github_pat="ghp_test123",
            s3_endpoint_url="https://s3.example.com",
            s3_bucket="test-bucket",
            s3_access_key="access123",
            s3_secret_key="secret123",
            data_dir=str(temp_dir),
        )

        assert settings.github_owner == "test-org"
        assert settings.github_pat == "ghp_test123"
        assert settings.s3_bucket == "test-bucket"

    def test_default_values(self, temp_dir):
        """Test that default values are set correctly."""
        settings = Settings(
            github_owner="test",
            github_pat="test",
            s3_endpoint_url="http://test",
            s3_bucket="test",
            s3_access_key="test",
            s3_secret_key="test",
            data_dir=str(temp_dir),
        )

        # Check defaults
        assert settings.github_backup_private is True
        assert settings.github_backup_forks is False
        assert settings.github_backup_archived is True
        assert settings.backup_retention_count == 7
        assert settings.backup_include_metadata is True
        assert settings.backup_include_wiki is True
        assert settings.backup_incremental is True
        assert settings.backup_schedule_enabled is True
        assert settings.backup_schedule_mode == "cron"
        assert settings.backup_schedule_hour == 2
        assert settings.backup_schedule_minute == 0
        assert settings.s3_region == "us-east-1"
        assert settings.alert_enabled is False
        assert settings.alert_level == "errors"
        assert settings.log_level == "INFO"

    def test_alert_channels_validation_valid(self, temp_dir):
        """Test valid alert channel configurations."""
        settings = Settings(
            github_owner="test",
            github_pat="test",
            s3_endpoint_url="http://test",
            s3_bucket="test",
            s3_access_key="test",
            s3_secret_key="test",
            alert_channels="email,webhook,teams",
            data_dir=str(temp_dir),
        )

        assert settings.get_alert_channels() == ["email", "webhook", "teams"]

    def test_alert_channels_validation_invalid(self, temp_dir):
        """Test that invalid alert channels are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Settings(
                github_owner="test",
                github_pat="test",
                s3_endpoint_url="http://test",
                s3_bucket="test",
                s3_access_key="test",
                s3_secret_key="test",
                alert_channels="email,invalid_channel",
                data_dir=str(temp_dir),
            )

        assert "Invalid alert channels" in str(exc_info.value)

    def test_day_of_week_validation_valid(self, temp_dir):
        """Test valid day of week configurations."""
        # All days
        settings = Settings(
            github_owner="test",
            github_pat="test",
            s3_endpoint_url="http://test",
            s3_bucket="test",
            s3_access_key="test",
            s3_secret_key="test",
            backup_schedule_day_of_week="*",
            data_dir=str(temp_dir),
        )
        assert settings.backup_schedule_day_of_week == "*"

        # Single day
        settings.backup_schedule_day_of_week = "0"
        assert settings.backup_schedule_day_of_week == "0"

        # Multiple days
        settings = Settings(
            github_owner="test",
            github_pat="test",
            s3_endpoint_url="http://test",
            s3_bucket="test",
            s3_access_key="test",
            s3_secret_key="test",
            backup_schedule_day_of_week="0,2,4",
            data_dir=str(temp_dir),
        )
        assert settings.backup_schedule_day_of_week == "0,2,4"

    def test_day_of_week_validation_invalid(self, temp_dir):
        """Test that invalid day of week is rejected."""
        with pytest.raises(ValidationError):
            Settings(
                github_owner="test",
                github_pat="test",
                s3_endpoint_url="http://test",
                s3_bucket="test",
                s3_access_key="test",
                s3_secret_key="test",
                backup_schedule_day_of_week="7",  # Invalid: 0-6 only
                data_dir=str(temp_dir),
            )

    def test_retention_count_minimum(self, temp_dir):
        """Test that retention count must be at least 1."""
        with pytest.raises(ValidationError):
            Settings(
                github_owner="test",
                github_pat="test",
                s3_endpoint_url="http://test",
                s3_bucket="test",
                s3_access_key="test",
                s3_secret_key="test",
                backup_retention_count=0,
                data_dir=str(temp_dir),
            )

    def test_get_smtp_recipients(self, temp_dir):
        """Test SMTP recipients parsing."""
        settings = Settings(
            github_owner="test",
            github_pat="test",
            s3_endpoint_url="http://test",
            s3_bucket="test",
            s3_access_key="test",
            s3_secret_key="test",
            smtp_to="admin@test.com, user@test.com , another@test.com",
            data_dir=str(temp_dir),
        )

        recipients = settings.get_smtp_recipients()
        assert recipients == ["admin@test.com", "user@test.com", "another@test.com"]

    def test_get_smtp_recipients_empty(self, temp_dir):
        """Test empty SMTP recipients."""
        settings = Settings(
            github_owner="test",
            github_pat="test",
            s3_endpoint_url="http://test",
            s3_bucket="test",
            s3_access_key="test",
            s3_secret_key="test",
            smtp_to="",
            data_dir=str(temp_dir),
        )

        assert settings.get_smtp_recipients() == []

    def test_alert_level_options(self, temp_dir):
        """Test that only valid alert levels are accepted."""
        for level in ["errors", "warnings", "all"]:
            settings = Settings(
                github_owner="test",
                github_pat="test",
                s3_endpoint_url="http://test",
                s3_bucket="test",
                s3_access_key="test",
                s3_secret_key="test",
                alert_level=level,
                data_dir=str(temp_dir),
            )
            assert settings.alert_level == level

        # Invalid level
        with pytest.raises(ValidationError):
            Settings(
                github_owner="test",
                github_pat="test",
                s3_endpoint_url="http://test",
                s3_bucket="test",
                s3_access_key="test",
                s3_secret_key="test",
                alert_level="invalid",
                data_dir=str(temp_dir),
            )

    def test_schedule_mode_options(self, temp_dir):
        """Test that only valid schedule modes are accepted."""
        for mode in ["cron", "interval"]:
            settings = Settings(
                github_owner="test",
                github_pat="test",
                s3_endpoint_url="http://test",
                s3_bucket="test",
                s3_access_key="test",
                s3_secret_key="test",
                backup_schedule_mode=mode,
                data_dir=str(temp_dir),
            )
            assert settings.backup_schedule_mode == mode
