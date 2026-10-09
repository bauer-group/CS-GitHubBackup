"""Tests for the backup scheduler (APScheduler 3)."""

import faulthandler
import os
import signal
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import scheduler as scheduler_module
from scheduler import JOB_ID, BackupScheduler

# A Sunday, after the 02:30 backup time used below
SUNDAY_NOON = datetime(2026, 1, 4, 12, 0)


@pytest.fixture
def make_scheduler(test_settings):
    """Build a BackupScheduler with S3 and the sync state mocked out."""

    def _make(backup_func=lambda: True, **overrides):
        settings = test_settings.model_copy(
            update={"backup_schedule_enabled": True, **overrides}
        )
        with patch.object(scheduler_module, "S3Storage"), patch.object(
            scheduler_module, "SyncStateManager"
        ):
            backup_scheduler = BackupScheduler(settings, backup_func)
        backup_scheduler.state_manager.should_run_backup.return_value = False
        return backup_scheduler

    return _make


@pytest.fixture
def lifecycle(monkeypatch):
    """Fast polling, a stand-in for main()'s SIGTERM handler, and a hang guard.

    Yields the signals that reached the stand-in handler. If the scheduler
    deadlocks on shutdown, faulthandler dumps the stacks and exits instead
    of hanging the build.
    """
    monkeypatch.setattr(scheduler_module, "POLL_INTERVAL_SECONDS", 0.01)

    received = []

    def main_handler(signum, frame):
        received.append(signum)

    original = signal.signal(signal.SIGTERM, main_handler)
    faulthandler.dump_traceback_later(120, exit=True)
    try:
        yield received
    finally:
        faulthandler.cancel_dump_traceback_later()
        restored = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, original)

    # start() must hand the signal handlers back when it returns
    assert restored is main_handler


def run_until_sigterm(backup_scheduler, ready, before_stop=None, timeout=30.0):
    """Run start() on the main thread and send SIGTERM once ``ready`` is set.

    ``before_stop`` runs on the helper thread while the scheduler is still
    running; once stopped, APScheduler 3 only looks up pending jobs.
    """

    def stopper():
        ready.wait(timeout)
        try:
            if before_stop:
                before_stop()
        finally:
            os.kill(os.getpid(), signal.SIGTERM)

    thread = threading.Thread(target=stopper, daemon=True)
    thread.start()
    backup_scheduler.start()
    thread.join(timeout)


class TestTriggers:
    """Verify the triggers built from the schedule settings."""

    @pytest.mark.parametrize(
        ("day_of_week", "expected_weekdays"),
        [
            ("0", [0]),
            ("6", [6]),
            ("0,2,4", [0, 2, 4]),
            ("*", [0, 1, 2, 3, 4, 5, 6]),
        ],
    )
    def test_numeric_weekdays_follow_documented_mapping(
        self, make_scheduler, day_of_week, expected_weekdays
    ) -> None:
        """Regression: APScheduler 4.0.0a6 read numeric weekdays crontab-style
        (0 = Sunday), so every weekly schedule fired a day early. The settings
        document 0 = Monday ... 6 = Sunday, which the 3.x trigger honours.
        """
        backup_scheduler = make_scheduler(
            backup_schedule_day_of_week=day_of_week,
            backup_schedule_hour=2,
            backup_schedule_minute=30,
        )
        trigger = backup_scheduler._create_trigger()
        assert isinstance(trigger, CronTrigger)

        now = SUNDAY_NOON.replace(tzinfo=trigger.timezone)
        fire_times = []
        for _ in expected_weekdays:
            now = trigger.get_next_fire_time(None, now)
            fire_times.append(now)
            now += timedelta(minutes=1)

        assert [fire.weekday() for fire in fire_times] == expected_weekdays
        assert all((fire.hour, fire.minute) == (2, 30) for fire in fire_times)

    def test_interval_mode_uses_configured_hours(self, make_scheduler) -> None:
        backup_scheduler = make_scheduler(
            backup_schedule_mode="interval",
            backup_schedule_interval_hours=6,
        )
        trigger = backup_scheduler._create_trigger()

        assert isinstance(trigger, IntervalTrigger)
        assert trigger.interval == timedelta(hours=6)


class TestLifecycle:
    """Run the scheduler for real and stop it the way Docker does (SIGTERM)."""

    def test_interval_mode_runs_first_backup_immediately(
        self, make_scheduler, lifecycle
    ) -> None:
        ran = threading.Event()

        def backup() -> bool:
            ran.set()
            return True

        backup_scheduler = make_scheduler(
            backup,
            backup_schedule_mode="interval",
            backup_schedule_interval_hours=1,
        )
        started_at = datetime.now(timezone.utc)
        next_runs = []

        # The scheduler moves the job to its next run under the job store
        # lock before releasing it, so this read sees the updated time
        run_until_sigterm(
            backup_scheduler,
            ran,
            before_stop=lambda: next_runs.append(
                backup_scheduler.scheduler.get_job(JOB_ID).next_run_time
            ),
        )

        assert ran.is_set()
        backup_scheduler.state_manager.update_sync_time.assert_called_once()
        assert len(next_runs) == 1
        assert timedelta(minutes=59) < next_runs[0] - started_at < timedelta(minutes=61)
        assert not backup_scheduler.scheduler.running
        # The signal was passed on to main()'s graceful shutdown handler
        assert lifecycle == [signal.SIGTERM]

    def test_shutdown_waits_for_running_backup(self, make_scheduler, lifecycle) -> None:
        """Regression: shutdown(wait=True) holds the job store lock while it
        waits for the running job, so a job listener that reads the schedule
        would deadlock. The backup must finish and start() must return.
        """
        started = threading.Event()
        finished = threading.Event()

        def backup() -> bool:
            started.set()
            # Keep running until shutdown has begun
            while backup_scheduler.scheduler.running:
                time.sleep(0.01)
            finished.set()
            return True

        backup_scheduler = make_scheduler(backup, backup_schedule_mode="interval")

        run_until_sigterm(backup_scheduler, started)

        assert finished.is_set()
        backup_scheduler.state_manager.update_sync_time.assert_called_once()

    def test_failed_backup_does_not_update_sync_state(
        self, make_scheduler, lifecycle
    ) -> None:
        attempted = threading.Event()

        def backup() -> bool:
            attempted.set()
            raise RuntimeError("simulated backup failure")

        backup_scheduler = make_scheduler(backup, backup_schedule_mode="interval")

        run_until_sigterm(backup_scheduler, attempted)

        assert attempted.is_set()
        backup_scheduler.state_manager.update_sync_time.assert_not_called()
