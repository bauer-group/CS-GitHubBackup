"""
GitHub Backup - Scheduler Module

Provides scheduled backup execution using APScheduler 3 with state persistence.
"""

import signal
import threading
import time
from datetime import datetime
from typing import Callable

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, JobExecutionEvent
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import Settings
from storage.s3_client import S3Storage
from sync_state_manager import SyncStateManager
from ui.console import backup_logger, console

JOB_ID = "github_backup"

# How often the main thread checks for a shutdown request
POLL_INTERVAL_SECONDS = 1.0


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
        self.scheduler: BackgroundScheduler | None = None
        self._job_finished = threading.Event()
        self._stop_requested = False

        # Initialize S3 storage and state manager with S3 sync
        self.s3_storage = S3Storage(settings)
        self.state_manager = SyncStateManager(settings.data_dir, self.s3_storage)

    def _run_backup_with_state(self) -> None:
        """Run backup and update sync state on success."""
        try:
            success = self.backup_func()
            if success:
                self.state_manager.update_sync_time()
                backup_logger.debug("Sync state updated after successful backup")
        except Exception as e:
            backup_logger.error(f"Backup execution failed: {e}")
            raise

    def _job_listener(self, event: JobExecutionEvent) -> None:
        """Handle job execution events.

        Runs in the executor's worker thread. It must not call back into the
        scheduler: shutdown() holds the job store lock while it waits for
        this thread, so a get_job() here would deadlock. The main loop
        prints the next run time instead.

        Args:
            event: Job execution event.
        """
        if event.exception:
            backup_logger.debug("Backup job failed")
            console.print("[red]Backup job failed[/]")
        else:
            backup_logger.debug("Backup job completed successfully")

        self._job_finished.set()

    def _get_next_run_time(self) -> datetime | None:
        """Return the next scheduled run time of the backup job, if any."""
        if not self.scheduler:
            return None

        job = self.scheduler.get_job(JOB_ID)
        return getattr(job, "next_run_time", None) if job else None

    def _print_next_run_time(self) -> None:
        """Print the next scheduled run time to the console."""
        try:
            next_run_time = self._get_next_run_time()
            if next_run_time:
                next_time = next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                console.print(f"\n[dim]Next backup scheduled for:[/] [cyan]{next_time}[/]")
                backup_logger.debug(f"Next backup scheduled for: {next_time}")
        except Exception as e:
            backup_logger.debug(f"Could not get next run time: {e}")

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
            backup_logger.warning("Scheduler is disabled in configuration")
            return

        # Check for missed backup on startup (only for cron mode)
        if self.settings.backup_schedule_mode != "interval":
            if self.state_manager.should_run_backup(
                self.settings.backup_schedule_hour,
                self.settings.backup_schedule_minute,
            ):
                console.print("[yellow]Missed scheduled backup detected, running now...[/]")
                backup_logger.debug("Running missed backup on startup")
                self._run_backup_with_state()

        # Create trigger based on mode
        trigger = self._create_trigger()

        # Print scheduler info
        schedule_desc = self._get_schedule_description()
        print_scheduler_info(schedule_desc)

        scheduler = BackgroundScheduler()
        self.scheduler = scheduler
        self._stop_requested = False

        # Subscribe to job events
        scheduler.add_listener(self._job_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

        # Interval schedules run once right away, then every N hours
        # (the APScheduler 4 behaviour this service was built on)
        first_run = {}
        if self.settings.backup_schedule_mode == "interval":
            first_run["next_run_time"] = datetime.now(scheduler.timezone)

        # One run at a time; a late run still starts and missed runs collapse
        # into one
        scheduler.add_job(
            self._run_backup_with_state,
            trigger,
            id=JOB_ID,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=None,
            **first_run,
        )

        # Start paused so the next run time is known before the first job runs
        scheduler.start(paused=True)

        # Pass signals on to the handlers main() installed, so a backup in
        # progress stops after its current repository. Importing main here
        # would load a second copy of it (it runs as __main__) whose shutdown
        # handler the running backup never checks.
        previous_handlers = {
            sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)
        }

        def signal_handler(signum, frame):
            signal_name = signal.Signals(signum).name
            backup_logger.debug(f"Received {signal_name}, stopping scheduler...")

            # Only set a flag; the loop below shuts the scheduler down
            self._stop_requested = True

            previous = previous_handlers.get(signum)
            if callable(previous):
                previous(signum, frame)

        try:
            for sig in previous_handlers:
                signal.signal(sig, signal_handler)

            # Show next run time
            next_run_time = self._get_next_run_time()
            if next_run_time:
                next_time = next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                console.print(f"[dim]Next backup:[/] [cyan]{next_time}[/]\n")
                backup_logger.debug(f"Next backup scheduled for: {next_time}")

            scheduler.resume()

            # Poll instead of blocking on a lock so the signal handler never
            # waits on a lock held by this thread
            while not self._stop_requested:
                time.sleep(POLL_INTERVAL_SECONDS)
                if self._job_finished.is_set():
                    self._job_finished.clear()
                    self._print_next_run_time()
        finally:
            # Lets a running backup finish; it stops after the current repository
            scheduler.shutdown(wait=True)
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
            backup_logger.debug("Scheduler stopped")


def setup_scheduler(settings: Settings, backup_func: Callable[[], bool]) -> BackupScheduler:
    """Create and configure the backup scheduler.

    Args:
        settings: Application settings.
        backup_func: Function to call for backup execution, returns success status.

    Returns:
        Configured BackupScheduler instance.
    """
    return BackupScheduler(settings, backup_func)
