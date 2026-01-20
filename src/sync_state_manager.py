"""
GitHub Backup - Sync State Manager

Persists backup state across container restarts to prevent duplicate backups.
Tracks per-repository state for incremental backup support.

State is stored locally and synced to S3 to survive container restarts
and data volume loss.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.s3_client import S3Storage

logger = logging.getLogger(__name__)


class RepoState:
    """State information for a single repository."""

    def __init__(
        self,
        pushed_at: Optional[str] = None,
        last_backup: Optional[str] = None,
        last_backup_id: Optional[str] = None,
    ):
        self.pushed_at = pushed_at
        self.last_backup = last_backup
        self.last_backup_id = last_backup_id

    def to_dict(self) -> dict:
        return {
            "pushed_at": self.pushed_at,
            "last_backup": self.last_backup,
            "last_backup_id": self.last_backup_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RepoState":
        return cls(
            pushed_at=data.get("pushed_at"),
            last_backup=data.get("last_backup"),
            last_backup_id=data.get("last_backup_id"),
        )


class SyncStateManager:
    """Manages backup sync state persistence.

    Stores the last successful backup timestamp and per-repository state,
    allowing the scheduler to determine whether a backup is needed and
    enabling incremental backups for unchanged repositories.

    State is stored locally and synced to S3 for persistence across
    container restarts and data volume loss.
    """

    STATE_FILE = "state.json"

    def __init__(self, data_dir: str = "/data", s3_storage: Optional["S3Storage"] = None):
        """Initialize sync state manager.

        Args:
            data_dir: Directory to store state file.
            s3_storage: Optional S3Storage instance for remote state sync.
        """
        self.state_file = Path(data_dir) / self.STATE_FILE
        self.s3_storage = s3_storage
        self._ensure_data_dir()
        self._state: Optional[dict] = None

        # Restore state from S3 on startup if local state is missing
        self._restore_state_from_s3()

    def _ensure_data_dir(self) -> None:
        """Ensure data directory exists."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def _restore_state_from_s3(self) -> None:
        """Restore state from S3 if local state is missing or outdated.

        Downloads state from S3 bucket when:
        - Local state file doesn't exist (fresh container/volume)
        - S3 state is newer than local state

        Deletes local state when:
        - Local state exists but S3 has no state (S3 was reset/changed)
        """
        if self.s3_storage is None:
            return

        if not self.state_file.exists():
            # No local state - try to restore from S3
            logger.info("No local state found, checking S3 for saved state...")
            if self.s3_storage.download_state(self.state_file):
                logger.info("State restored from S3")
            else:
                logger.debug("No state in S3 (first run)")
        else:
            # Local state exists - verify S3 also has state
            # If S3 has no state, it means S3 was reset/changed and local state is invalid
            if not self.s3_storage.state_exists():
                logger.warning(
                    "Local state exists but S3 has no state - "
                    "S3 storage was likely reset. Discarding local state."
                )
                self.state_file.unlink()
                self._state = None
                logger.info("Local state discarded, starting fresh")

    def _sync_state_to_s3(self) -> None:
        """Upload state to S3 for persistence."""
        if self.s3_storage is None:
            return

        if self.s3_storage.upload_state(self.state_file):
            logger.debug("State synced to S3")

    def set_s3_storage(self, s3_storage: "S3Storage") -> None:
        """Set S3 storage for state sync (can be called after init).

        Args:
            s3_storage: S3Storage instance for remote state sync.
        """
        self.s3_storage = s3_storage
        # Try to restore state now that we have S3
        self._restore_state_from_s3()

    def _load_state(self) -> dict:
        """Load state from file."""
        if self._state is not None:
            return self._state

        if not self.state_file.exists():
            self._state = {"repositories": {}}
            return self._state

        try:
            with open(self.state_file, "r") as f:
                self._state = json.load(f)
                # Ensure repositories dict exists (backward compatibility)
                if "repositories" not in self._state:
                    self._state["repositories"] = {}
                return self._state
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to read sync state: {e}")
            self._state = {"repositories": {}}
            return self._state

    def _save_state(self) -> None:
        """Save state to file and sync to S3."""
        if self._state is None:
            return

        self._state["updated_at"] = datetime.now().isoformat()

        try:
            with open(self.state_file, "w") as f:
                json.dump(self._state, f, indent=2)
            # Sync to S3 for persistence
            self._sync_state_to_s3()
        except IOError as e:
            logger.error(f"Failed to write sync state: {e}")

    def get_last_sync_time(self) -> Optional[datetime]:
        """Get the timestamp of the last successful backup.

        Returns:
            Last sync datetime or None if no state exists.
        """
        state = self._load_state()
        last_sync = state.get("last_sync")
        if last_sync:
            try:
                return datetime.fromisoformat(last_sync)
            except ValueError:
                return None
        return None

    def update_sync_time(self, sync_time: Optional[datetime] = None) -> None:
        """Update the last sync timestamp.

        Args:
            sync_time: Timestamp to store. Uses current time if not provided.
        """
        if sync_time is None:
            sync_time = datetime.now()

        state = self._load_state()
        state["last_sync"] = sync_time.isoformat()
        self._save_state()
        logger.debug(f"Updated sync state: {sync_time.isoformat()}")

    # === Repository State Methods ===

    def get_repo_state(self, repo_name: str) -> Optional[RepoState]:
        """Get state for a specific repository.

        Args:
            repo_name: Repository name.

        Returns:
            RepoState or None if not tracked.
        """
        state = self._load_state()
        repo_data = state["repositories"].get(repo_name)
        if repo_data:
            return RepoState.from_dict(repo_data)
        return None

    def update_repo_state(
        self,
        repo_name: str,
        pushed_at: str,
        backup_id: str,
    ) -> None:
        """Update state for a repository after successful backup.

        Args:
            repo_name: Repository name.
            pushed_at: GitHub pushed_at timestamp.
            backup_id: Backup identifier where this repo was backed up.
        """
        state = self._load_state()
        state["repositories"][repo_name] = {
            "pushed_at": pushed_at,
            "last_backup": datetime.now().isoformat(),
            "last_backup_id": backup_id,
        }
        self._save_state()
        logger.debug(f"Updated repo state: {repo_name} -> {backup_id}")

    def has_repo_changed(self, repo_name: str, current_pushed_at: str) -> bool:
        """Check if a repository has changed since last backup.

        Args:
            repo_name: Repository name.
            current_pushed_at: Current pushed_at timestamp from GitHub.

        Returns:
            True if repo has changed or was never backed up.
        """
        repo_state = self.get_repo_state(repo_name)

        if repo_state is None:
            logger.debug(f"{repo_name}: No previous backup, needs backup")
            return True

        if repo_state.pushed_at is None:
            logger.debug(f"{repo_name}: No pushed_at in state, needs backup")
            return True

        if current_pushed_at != repo_state.pushed_at:
            logger.debug(
                f"{repo_name}: Changed (was {repo_state.pushed_at}, now {current_pushed_at})"
            )
            return True

        logger.debug(f"{repo_name}: Unchanged since {repo_state.last_backup}")
        return False

    def get_last_backup_id(self, repo_name: str) -> Optional[str]:
        """Get the last backup ID for a repository.

        Args:
            repo_name: Repository name.

        Returns:
            Backup ID or None if never backed up.
        """
        repo_state = self.get_repo_state(repo_name)
        if repo_state:
            return repo_state.last_backup_id
        return None

    def get_backed_up_repos(self) -> dict[str, str]:
        """Get all repositories with their last backup IDs.

        Returns:
            Dict mapping repo names to their last backup IDs.
        """
        state = self._load_state()
        return {
            name: data.get("last_backup_id")
            for name, data in state["repositories"].items()
            if data.get("last_backup_id")
        }

    def remove_repo_state(self, repo_name: str) -> None:
        """Remove state for a repository.

        Args:
            repo_name: Repository name.
        """
        state = self._load_state()
        if repo_name in state["repositories"]:
            del state["repositories"][repo_name]
            self._save_state()
            logger.debug(f"Removed repo state: {repo_name}")

    def should_run_backup(self, schedule_hour: int, schedule_minute: int) -> bool:
        """Determine if a backup should run based on last sync time.

        Used after container restart to check if the scheduled backup was missed.

        Args:
            schedule_hour: Configured backup hour (0-23).
            schedule_minute: Configured backup minute (0-59).

        Returns:
            True if backup should run (was missed), False otherwise.
        """
        last_sync = self.get_last_sync_time()

        if last_sync is None:
            logger.info("No previous backup found, backup recommended")
            return True

        now = datetime.now()

        # Calculate when the last scheduled backup should have occurred
        today_scheduled = now.replace(
            hour=schedule_hour,
            minute=schedule_minute,
            second=0,
            microsecond=0,
        )

        if now < today_scheduled:
            # Before today's schedule time - check yesterday
            last_scheduled = today_scheduled - timedelta(days=1)
        else:
            # After today's schedule time
            last_scheduled = today_scheduled

        # If last sync was before the last scheduled time, we missed a backup
        if last_sync < last_scheduled:
            logger.info(
                f"Missed scheduled backup at {last_scheduled.isoformat()}, "
                f"last sync was {last_sync.isoformat()}"
            )
            return True

        logger.debug(
            f"No missed backup: last sync {last_sync.isoformat()} "
            f"is after scheduled {last_scheduled.isoformat()}"
        )
        return False

    def clear_state(self) -> None:
        """Remove the sync state file."""
        if self.state_file.exists():
            self.state_file.unlink()
            logger.debug("Cleared sync state")
