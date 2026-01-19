"""
GitHub Backup - Email Alerter

Sends backup alerts via SMTP with HTML and plain text content.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from config import Settings
from alerting.base import AlertData, AlertLevel, BaseAlerter

logger = logging.getLogger(__name__)


class EmailAlerter(BaseAlerter):
    """Sends alerts via SMTP email with HTML formatting."""

    def __init__(self, settings: Settings):
        """Initialize email alerter.

        Args:
            settings: Application settings with SMTP configuration.
        """
        self.settings = settings

    def _get_smtp_connection(self) -> smtplib.SMTP:
        """Create and return SMTP connection."""
        if self.settings.smtp_ssl:
            smtp = smtplib.SMTP_SSL(
                self.settings.smtp_host,
                self.settings.smtp_port,
                timeout=30,
            )
        else:
            smtp = smtplib.SMTP(
                self.settings.smtp_host,
                self.settings.smtp_port,
                timeout=30,
            )
            if self.settings.smtp_tls:
                smtp.starttls()

        if self.settings.smtp_user and self.settings.smtp_password:
            smtp.login(self.settings.smtp_user, self.settings.smtp_password)

        return smtp

    def send(self, alert: AlertData) -> bool:
        """Send alert email.

        Args:
            alert: Alert data to send.

        Returns:
            True if email was sent successfully.
        """
        recipients = self.settings.get_smtp_recipients()
        if not recipients:
            logger.warning("No email recipients configured")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = self._build_subject(alert)
            msg["From"] = f"{self.settings.smtp_from_name} <{self.settings.smtp_from}>"
            msg["To"] = ", ".join(recipients)

            # Plain text version
            plain_text = self._build_plain_text(alert)
            msg.attach(MIMEText(plain_text, "plain", "utf-8"))

            # HTML version
            html_content = self._build_html(alert)
            msg.attach(MIMEText(html_content, "html", "utf-8"))

            with self._get_smtp_connection() as smtp:
                smtp.sendmail(
                    self.settings.smtp_from,
                    recipients,
                    msg.as_string(),
                )

            logger.info(f"Alert email sent to {len(recipients)} recipient(s)")
            return True

        except Exception as e:
            logger.error(f"Failed to send alert email: {e}")
            return False

    def test_connection(self) -> bool:
        """Test SMTP connection.

        Returns:
            True if connection is working.
        """
        try:
            with self._get_smtp_connection() as smtp:
                smtp.noop()
            logger.info("SMTP connection test successful")
            return True
        except Exception as e:
            logger.error(f"SMTP connection test failed: {e}")
            return False

    def _build_subject(self, alert: AlertData) -> str:
        """Build email subject line."""
        prefix = {
            AlertLevel.SUCCESS: "✓",
            AlertLevel.WARNING: "⚠",
            AlertLevel.ERROR: "✗",
        }[alert.level]

        return f"{prefix} GitHub Backup: {alert.title}"

    def _build_plain_text(self, alert: AlertData) -> str:
        """Build plain text email body."""
        lines = [
            f"GitHub Backup - {alert.title}",
            "=" * 50,
            "",
            f"Status: {alert.level.value.upper()}",
            f"Backup ID: {alert.backup_id}",
            f"Time: {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]

        if alert.github_owner:
            lines.append(f"GitHub Owner: {alert.github_owner}")

        lines.extend([
            "",
            "Backup Summary",
            "-" * 20,
            f"Repositories Backed Up: {alert.repos_backed_up}",
        ])

        if alert.repos_skipped > 0:
            lines.append(f"Repositories Skipped: {alert.repos_skipped} (unchanged)")

        if alert.repos_failed > 0:
            lines.append(f"Repositories Failed: {alert.repos_failed}")

        if alert.repos_backed_up > 0:
            lines.extend([
                f"Issues: {alert.issues_count}",
                f"Pull Requests: {alert.prs_count}",
                f"Releases: {alert.releases_count}",
                f"Wikis: {alert.wikis_count}",
            ])

            if alert.total_size_bytes > 0:
                lines.append(f"Total Size: {alert.format_size()}")

        lines.append(f"Duration: {alert.format_duration()}")

        if alert.deleted_backups > 0:
            lines.append(f"Old Backups Removed: {alert.deleted_backups}")

        if alert.error_messages:
            lines.extend([
                "",
                "Errors",
                "-" * 20,
            ])
            for error in alert.error_messages:
                lines.append(f"  • {error}")

        lines.extend([
            "",
            "-" * 50,
            "GitHub Backup System",
        ])

        return "\n".join(lines)

    def _build_html(self, alert: AlertData) -> str:
        """Build HTML email body with professional styling."""
        # Color based on alert level
        status_colors = {
            AlertLevel.SUCCESS: ("#28a745", "#d4edda", "#155724"),  # green
            AlertLevel.WARNING: ("#ffc107", "#fff3cd", "#856404"),  # yellow
            AlertLevel.ERROR: ("#dc3545", "#f8d7da", "#721c24"),    # red
        }
        accent_color, bg_color, text_color = status_colors[alert.level]

        # Status icon
        status_icons = {
            AlertLevel.SUCCESS: "✓",
            AlertLevel.WARNING: "⚠",
            AlertLevel.ERROR: "✗",
        }
        status_icon = status_icons[alert.level]

        # Build stats rows
        stats_rows = f"""
            <tr>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0;">Repositories Backed Up</td>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0; text-align: right; font-weight: 600;">{alert.repos_backed_up}</td>
            </tr>
        """

        if alert.repos_skipped > 0:
            stats_rows += f"""
            <tr>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0;">Repositories Skipped</td>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0; text-align: right; color: #666;">{alert.repos_skipped} <span style="font-size: 12px;">(unchanged)</span></td>
            </tr>
            """

        if alert.repos_failed > 0:
            stats_rows += f"""
            <tr>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0;">Repositories Failed</td>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0; text-align: right; color: #dc3545; font-weight: 600;">{alert.repos_failed}</td>
            </tr>
            """

        if alert.repos_backed_up > 0:
            stats_rows += f"""
            <tr>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0;">Issues</td>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0; text-align: right;">{alert.issues_count}</td>
            </tr>
            <tr>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0;">Pull Requests</td>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0; text-align: right;">{alert.prs_count}</td>
            </tr>
            <tr>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0;">Releases</td>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0; text-align: right;">{alert.releases_count}</td>
            </tr>
            <tr>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0;">Wikis</td>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0; text-align: right;">{alert.wikis_count}</td>
            </tr>
            """

            if alert.total_size_bytes > 0:
                stats_rows += f"""
            <tr>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0;">Total Size</td>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0; text-align: right;">{alert.format_size()}</td>
            </tr>
                """

        stats_rows += f"""
            <tr>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0;">Duration</td>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0; text-align: right;">{alert.format_duration()}</td>
            </tr>
        """

        if alert.deleted_backups > 0:
            stats_rows += f"""
            <tr>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0;">Old Backups Removed</td>
                <td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0; text-align: right;">{alert.deleted_backups}</td>
            </tr>
            """

        # Build errors section if any
        errors_section = ""
        if alert.error_messages:
            error_items = "".join([
                f'<li style="margin-bottom: 8px; color: #721c24;">{error}</li>'
                for error in alert.error_messages
            ])
            errors_section = f"""
            <div style="margin-top: 24px; padding: 16px; background-color: #f8d7da; border-radius: 8px; border-left: 4px solid #dc3545;">
                <h3 style="margin: 0 0 12px 0; color: #721c24; font-size: 16px;">Errors</h3>
                <ul style="margin: 0; padding-left: 20px;">
                    {error_items}
                </ul>
            </div>
            """

        # Owner info
        owner_info = ""
        if alert.github_owner:
            owner_info = f"""
            <p style="margin: 8px 0; color: #666; font-size: 14px;">
                GitHub Owner: <strong>{alert.github_owner}</strong>
            </p>
            """

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GitHub Backup Alert</title>
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif; background-color: #f5f5f5;">
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f5f5f5;">
        <tr>
            <td style="padding: 20px;">
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="600" style="margin: 0 auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                    <!-- Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #24292e 0%, #1a1e22 100%); padding: 24px 32px;">
                            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                                <tr>
                                    <td>
                                        <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">
                                            GitHub Backup
                                        </h1>
                                    </td>
                                    <td style="text-align: right;">
                                        <span style="display: inline-block; padding: 6px 12px; background-color: {bg_color}; color: {text_color}; border-radius: 16px; font-size: 14px; font-weight: 600;">
                                            {status_icon} {alert.level.value.upper()}
                                        </span>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <!-- Content -->
                    <tr>
                        <td style="padding: 32px;">
                            <!-- Title -->
                            <h2 style="margin: 0 0 8px 0; color: #24292e; font-size: 20px; font-weight: 600;">
                                {alert.title}
                            </h2>

                            <!-- Meta Info -->
                            <p style="margin: 8px 0; color: #666; font-size: 14px;">
                                Backup ID: <strong>{alert.backup_id}</strong>
                            </p>
                            <p style="margin: 8px 0; color: #666; font-size: 14px;">
                                Time: <strong>{alert.timestamp.strftime('%Y-%m-%d %H:%M:%S')}</strong>
                            </p>
                            {owner_info}

                            <!-- Message -->
                            <p style="margin: 20px 0; color: #24292e; font-size: 15px; line-height: 1.5;">
                                {alert.message}
                            </p>

                            <!-- Stats Table -->
                            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin-top: 24px; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
                                <tr>
                                    <td colspan="2" style="background-color: #f8f9fa; padding: 12px 12px; border-bottom: 1px solid #e0e0e0;">
                                        <strong style="color: #24292e; font-size: 14px;">Backup Summary</strong>
                                    </td>
                                </tr>
                                {stats_rows}
                            </table>

                            {errors_section}
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f8f9fa; padding: 20px 32px; border-top: 1px solid #e0e0e0;">
                            <p style="margin: 0; color: #666; font-size: 12px; text-align: center;">
                                GitHub Backup System &bull; Automated backup notification
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

        return html
