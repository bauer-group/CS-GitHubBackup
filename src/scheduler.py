"""
GitHub Backup - Scheduler Module

Provides scheduled backup execution using APScheduler v4 with state persistence.
"""

import logging
import signal
import sys
from typing import Callable

from apscheduler import Scheduler, Event, JobReleased
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import Settings
from storage.s3_client import S3Storage
from sync_state_manager import SyncStateManager
from ui.console import console

logger = logging.getLogger(__name__)


class BackupScheduler:
    """Manages scheduled backup execution with state persistence."""

    def __init__(self, settings: Settings, backup_func: Callable[[], bool]):
        """Initialize the backup scheduler.

        Args:
            settings: Application settings.
            backup_func: Function to call for backup execution, returns success status.
        """
        self.settings = settings
        self.backup_func = backup_func
        self.scheduler: Scheduler | None = None

        # Initialize S3 storage and state manager with S3 sync
        self.s3_storage = S3Storage(settings)
        self.state_manager = SyncStateManager(settings.data_dir, self.s3_storage)

    def _run_backup_with_state(self) -> None:
        """Run backup and update sync state on success."""
        try:
            success = self.backup_func()
            if success:
                self.state_manager.update_sync_time()
                logger.info("Sync state updated after successful backup")
        except Exception as e:
            logger.error(f"Backup execution failed: {e}")
            raise

    def _job_listener(self, event: Event) -> None:
        """Handle job execution events.

        Args:
            event: Job execution event.
        """
        if isinstance(event, JobReleased):
            if event.outcome and event.outcome.name == "error":
                logger.error(f"Backup job failed")
                console.print(f"[red]Backup job failed[/]")
            else:
                logger.info("Backup job completed successfully")

    def _create_trigger(self):
        """Create the appropriate trigger based on schedule mode.

        Returns:
            APScheduler trigger instance.
        """
        mode = self.settings.backup_schedule_mode
        hour = self.settings.backup_schedule_hour
        minute = self.settings.backup_schedule_minute
        day_of_week = self.settings.backup_schedule_day_of_week
        interval_hours = self.settings.backup_schedule_interval_hours

        if mode == "interval":
            return IntervalTrigger(hours=interval_hours)
        else:  # cron (default)
            return CronTrigger(
                day_of_week=day_of_week,
                hour=hour,
                minute=minute,
            )

    def _get_schedule_description(self) -> str:
        """Get human-readable schedule description.

        Returns:
            Schedule description string.
        """
        mode = self.settings.backup_schedule_mode
        hour = self.settings.backup_schedule_hour
        minute = self.settings.backup_schedule_minute
        day_of_week = self.settings.backup_schedule_day_of_week
        interval_hours = self.settings.backup_schedule_interval_hours

        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        if mode == "interval":
            if interval_hours == 1:
                return "Every hour"
            else:
                return f"Every {interval_hours} hours"
        else:  # cron
            if day_of_week == "*":
                return f"Daily at {hour:02d}:{minute:02d}"
            else:
                days = [day_names[int(d.strip())] for d in day_of_week.split(",")]
                if len(days) == 1:
                    return f"Weekly on {days[0]} at {hour:02d}:{minute:02d}"
                else:
                    return f"On {', '.join(days)} at {hour:02d}:{minute:02d}"

    def start(self) -> None:
        """Start the scheduler.

        Checks for missed backups on startup and runs them if needed.
        """
        from ui.console import print_scheduler_info

        if not self.settings.backup_schedule_enabled:
            logger.warning("Scheduler is disabled in configuration")
            console.print("[yellow]Scheduler is disabled. Use --now to run immediately.[/]")
            return

        # Check for missed backup on startup (only for cron mode)
        if self.settings.backup_schedule_mode != "interval":
            if self.state_manager.should_run_backup(
                self.settings.backup_schedule_hour,
                self.settings.backup_schedule_minute,
            ):
                console.print("[yellow]Missed scheduled backup detected, running now...[/]")
                logger.info("Running missed backup on startup")
                self._run_backup_with_state()

        # Create trigger based on mode
        trigger = self._create_trigger()

        # Print scheduler info
        schedule_desc = self._get_schedule_description()
        print_scheduler_info(schedule_desc)

        # Use context manager for scheduler
        with Scheduler() as scheduler:
            self.scheduler = scheduler

            # Setup signal handlers inside context
            def signal_handler(signum, frame):
                logger.info("Received shutdown signal, stopping scheduler...")
                console.print("\n[yellow]Shutting down scheduler...[/]")
                scheduler.stop()

            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)

            # Subscribe to job events
            scheduler.subscribe(self._job_listener, {JobReleased})

            # Add schedule
            schedule_id = scheduler.add_schedule(
                self._run_backup_with_state,
                trigger,
                id="github_backup",
            )

            # Log next run time
            try:
                schedule = scheduler.get_schedule(schedule_id)
                if schedule and schedule.next_fire_time:
                    logger.info(f"Next backup scheduled for: {schedule.next_fire_time}")
            except Exception:
                pass  # Ignore if we can't get schedule info

            # Start the scheduler (blocks)
            try:
                scheduler.run_until_stopped()
            except (KeyboardInterrupt, SystemExit):
                logger.info("Scheduler stopped")


def setup_scheduler(settings: Settings, backup_func: Callable[[], bool]) -> BackupScheduler:
    """Create and configure the backup scheduler.

    Args:
        settings: Application settings.
        backup_func: Function to call for backup execution, returns success status.

    Returns:
        Configured BackupScheduler instance.
    """
    return BackupScheduler(settings, backup_func)
