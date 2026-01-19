"""
GitHub Backup - Alert Base Types

Defines alert levels, data structures, and base alerter interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class AlertLevel(Enum):
    """Alert severity levels with semantic colors."""

    SUCCESS = "success"  # Green - backup completed successfully
    WARNING = "warning"  # Yellow - backup completed with issues
    ERROR = "error"      # Red - backup failed

    @property
    def color_hex(self) -> str:
        """Get hex color code for this level."""
        colors = {
            AlertLevel.SUCCESS: "28a745",  # Green
            AlertLevel.WARNING: "ffc107",  # Yellow/Amber
            AlertLevel.ERROR: "dc3545",    # Red
        }
        return colors[self]

    @property
    def color_name(self) -> str:
        """Get color name for this level."""
        colors = {
            AlertLevel.SUCCESS: "green",
            AlertLevel.WARNING: "yellow",
            AlertLevel.ERROR: "red",
        }
        return colors[self]

    @property
    def emoji(self) -> str:
        """Get emoji for this level."""
        emojis = {
            AlertLevel.SUCCESS: "✓",
            AlertLevel.WARNING: "⚠",
            AlertLevel.ERROR: "✗",
        }
        return emojis[self]


@dataclass
class AlertData:
    """Data structure for backup alerts."""

    level: AlertLevel
    title: str
    message: str
    backup_id: str
    timestamp: datetime = field(default_factory=datetime.now)

    # Backup statistics
    repos_backed_up: int = 0
    repos_skipped: int = 0
    repos_failed: int = 0
    total_repos: int = 0

    # Metadata counts
    issues_count: int = 0
    prs_count: int = 0
    releases_count: int = 0
    wikis_count: int = 0

    # Size and duration
    total_size_bytes: int = 0
    duration_seconds: float = 0

    # Errors
    error_messages: list[str] = field(default_factory=list)

    # Optional details
    github_owner: Optional[str] = None
    deleted_backups: int = 0

    def format_size(self) -> str:
        """Format total size as human-readable string."""
        size = float(self.total_size_bytes)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    def format_duration(self) -> str:
        """Format duration as human-readable string."""
        seconds = self.duration_seconds
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"


class BaseAlerter(ABC):
    """Base class for alert senders."""

    @abstractmethod
    def send(self, alert: AlertData) -> bool:
        """Send an alert.

        Args:
            alert: Alert data to send.

        Returns:
            True if alert was sent successfully.
        """
        pass

    @abstractmethod
    def test_connection(self) -> bool:
        """Test the alerter connection/configuration.

        Returns:
            True if connection is working.
        """
        pass
