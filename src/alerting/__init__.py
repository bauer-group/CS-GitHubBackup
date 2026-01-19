"""
GitHub Backup - Alerting Module

Provides alerting capabilities via Email, Webhooks, and Microsoft Teams.
"""

from alerting.base import AlertLevel, AlertData
from alerting.manager import AlertManager

__all__ = ["AlertLevel", "AlertData", "AlertManager"]
