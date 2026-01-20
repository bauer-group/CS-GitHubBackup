#!/usr/bin/env python3
"""
GitHub Backup - Main Entry Point

Automated backup of GitHub repositories to S3-compatible storage.
"""

import logging
import shutil
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from config import Settings
from scheduler import setup_scheduler
from sync_state_manager import SyncStateManager
from alerting.manager import AlertManager
from backup.github_client import GitHubBackupClient
from backup.git_operations import GitBackup, BackupResult
from backup.metadata_exporter import MetadataExporter
from backup.wiki_backup import WikiBackup
from storage.s3_client import S3Storage
from ui.console import (
    backup_logger,
    console,
    create_progress,
    print_banner,
    print_completion,
    print_error,
    print_repo_status,
    print_summary,
    setup_logging,
    format_size,
)


class ShutdownHandler:
    """Handles graceful shutdown on SIGTERM/SIGINT.

    Allows the current repository backup to complete before exiting.
    """

    def __init__(self):
        self._shutdown_requested = threading.Event()
        self._current_repo: str | None = None
        self._lock = threading.Lock()

    def request_shutdown(self, signum: int, frame) -> None:
        """Signal handler for shutdown requests."""
        signal_name = signal.Signals(signum).name
        backup_logger.debug(f"Received {signal_name}, initiating graceful shutdown...")

        with self._lock:
            if self._current_repo:
                console.print(
                    f"\n[yellow]Shutdown requested - completing backup of "
                    f"[cyan]{self._current_repo}[/] before exit...[/]"
                )
            else:
                console.print("\n[yellow]Shutdown requested - stopping...[/]")

        self._shutdown_requested.set()

    def is_shutdown_requested(self) -> bool:
        """Check if shutdown has been requested."""
        return self._shutdown_requested.is_set()

    def set_current_repo(self, repo_name: str | None) -> None:
        """Set the currently processing repository name."""
        with self._lock:
            self._current_repo = repo_name

    def get_current_repo(self) -> str | None:
        """Get the currently processing repository name."""
        with self._lock:
            return self._current_repo


# Global shutdown handler instance
shutdown_handler = ShutdownHandler()


def run_backup(settings: Settings) -> bool:
    """Execute a backup of repositories.

    If incremental mode is enabled, only repositories that have changed
    since the last backup will be backed up.

    Args:
        settings: Application settings.

    Returns:
        True if backup completed successfully.
    """
    start_time = time.time()
    backup_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    work_dir = Path(settings.data_dir) / backup_id

    # Initialize state manager for incremental backup tracking
    state_manager = SyncStateManager(settings.data_dir)

    # Initialize alert manager
    alert_manager = AlertManager(settings)

    # Initialize statistics
    stats = {
        "repos": 0,
        "skipped": 0,
        "issues": 0,
        "prs": 0,
        "releases": 0,
        "wikis": 0,
        "total_size": 0,
        "errors": 0,
    }

    # Collect error messages for alerting
    error_messages = []

    try:
        # Initialize clients
        console.print("\n[dim]Initializing...[/]")
        gh_client = GitHubBackupClient(settings)
        s3_storage = S3Storage(settings)

        # Ensure bucket exists
        if not s3_storage.ensure_bucket_exists():
            print_error("Failed to access or create S3 bucket")
            return False

        # Connect state manager to S3 for persistence
        state_manager.set_s3_storage(s3_storage)

        # Get repositories
        console.print(f"[dim]Fetching repositories for {settings.github_owner}...[/]")
        repos = list(gh_client.get_repositories())
        total_repos = len(repos)

        if total_repos == 0:
            console.print("[yellow]No repositories found matching criteria[/]")
            return True

        # Check which repos need backup (if incremental mode)
        repos_to_backup = []
        if settings.backup_incremental:
            for repo_info in repos:
                if state_manager.has_repo_changed(repo_info.name, repo_info.pushed_at):
                    repos_to_backup.append(repo_info)
                else:
                    stats["skipped"] += 1

            console.print(
                f"[green]Found {total_repos} repositories, "
                f"{len(repos_to_backup)} changed, "
                f"{stats['skipped']} unchanged[/]\n"
            )
        else:
            repos_to_backup = repos
            console.print(f"[green]Found {total_repos} repositories to backup[/]\n")

        # If no repos need backup, we're done (but this is still success)
        if len(repos_to_backup) == 0:
            console.print("[green]All repositories are up to date, no backup needed[/]")
            # Print summary even when skipping
            duration = time.time() - start_time
            print_summary(stats, duration)
            print_completion(True)
            return True

        # Print banner only if we have work to do
        print_banner(backup_id)

        # Ensure work directory exists
        work_dir.mkdir(parents=True, exist_ok=True)

        # Initialize backup components
        git_backup = GitBackup(work_dir)
        metadata_exporter = MetadataExporter(work_dir)
        wiki_backup = WikiBackup(git_backup)

        # Track if we're stopping early due to shutdown
        shutdown_early = False
        repos_remaining = 0

        # Process repositories with progress bar
        with create_progress() as progress:
            task = progress.add_task("Backing up repositories", total=len(repos_to_backup))

            for idx, repo_info in enumerate(repos_to_backup):
                # Check for shutdown request before starting new repo
                if shutdown_handler.is_shutdown_requested():
                    repos_remaining = len(repos_to_backup) - idx
                    shutdown_early = True
                    console.print(
                        f"\n[yellow]Shutdown: Skipping {repos_remaining} remaining "
                        f"repositories[/]"
                    )
                    break

                repo_name = repo_info.name
                shutdown_handler.set_current_repo(repo_name)

                repo_stats = {
                    "git_size": None,
                    "lfs_size": None,
                    "has_lfs": False,
                    "issues": None,
                    "prs": None,
                    "releases": None,
                    "wiki": None,
                    "error": None,
                }

                try:
                    # Backup git repository (including LFS if present)
                    clone_url = gh_client.get_clone_url(repo_info)
                    backup_result = git_backup.clone_and_bundle(
                        clone_url, repo_name
                    )

                    if backup_result.is_empty:
                        # Empty repository - no commits
                        repo_stats["git_size"] = "empty"
                    elif backup_result.bundle_path is not None:
                        repo_stats["git_size"] = format_size(backup_result.bundle_size)
                        stats["total_size"] += backup_result.bundle_size
                        # Upload bundle to S3
                        s3_storage.upload_file(backup_result.bundle_path, backup_id, repo_name)

                        # Upload LFS archive if present
                        if backup_result.lfs_path is not None:
                            repo_stats["has_lfs"] = True
                            repo_stats["lfs_size"] = format_size(backup_result.lfs_size)
                            stats["total_size"] += backup_result.lfs_size
                            stats["lfs_repos"] = stats.get("lfs_repos", 0) + 1
                            s3_storage.upload_file(backup_result.lfs_path, backup_id, repo_name)

                    # Backup metadata (use underlying repo object)
                    if settings.backup_include_metadata:
                        meta_counts = metadata_exporter.export_all(repo_info.repo)
                        repo_stats["issues"] = meta_counts["issues"]
                        repo_stats["prs"] = meta_counts["prs"]
                        repo_stats["releases"] = meta_counts["releases"]
                        stats["issues"] += meta_counts["issues"]
                        stats["prs"] += meta_counts["prs"]
                        stats["releases"] += meta_counts["releases"]

                        # Upload metadata to S3
                        metadata_dir = work_dir / repo_name / "metadata"
                        if metadata_dir.exists():
                            s3_storage.upload_directory(metadata_dir, backup_id, repo_name)

                    # Backup wiki
                    if settings.backup_include_wiki:
                        wiki_url = gh_client.get_wiki_url(repo_info)
                        wiki_path, wiki_size = wiki_backup.backup_wiki(wiki_url, repo_name)
                        if wiki_path:
                            repo_stats["wiki"] = True
                            stats["wikis"] += 1
                            stats["total_size"] += wiki_size
                            s3_storage.upload_file(wiki_path, backup_id, repo_name)
                        else:
                            repo_stats["wiki"] = False

                    stats["repos"] += 1

                    # Update repo state after successful backup
                    state_manager.update_repo_state(
                        repo_name=repo_name,
                        pushed_at=repo_info.pushed_at,
                        backup_id=backup_id,
                    )

                except Exception as e:
                    backup_logger.debug(f"Failed to backup {repo_name}: {e}")
                    repo_stats["error"] = str(e)
                    stats["errors"] += 1
                    error_messages.append(f"{repo_name}: {e}")

                # Print status for this repo
                print_repo_status(
                    repo_name,
                    git_size=repo_stats["git_size"],
                    has_lfs=repo_stats["has_lfs"],
                    lfs_size=repo_stats["lfs_size"],
                    issues=repo_stats["issues"],
                    prs=repo_stats["prs"],
                    releases=repo_stats["releases"],
                    wiki=repo_stats["wiki"],
                    error=repo_stats["error"],
                )

                # Clear current repo tracking
                shutdown_handler.set_current_repo(None)

                progress.advance(task)

        # Cleanup old backups (smart retention - preserves last backup per repo)
        # Skip if shutting down to exit faster
        if not shutdown_early:
            console.print("\n[dim]Checking retention policy...[/]")
            deleted_count = s3_storage.cleanup_old_backups(
                state_manager.get_backed_up_repos()
            )
            if deleted_count > 0:
                stats["deleted_backups"] = deleted_count

        # Cleanup local work directory
        console.print("[dim]Cleaning up local files...[/]")
        shutil.rmtree(work_dir, ignore_errors=True)

        # Print summary
        duration = time.time() - start_time
        if shutdown_early:
            stats["shutdown_skipped"] = repos_remaining
        print_summary(stats, duration)

        # Print completion status
        success = stats["errors"] == 0
        if shutdown_early:
            console.print("\n[yellow]Backup stopped early due to shutdown request[/]")
        print_completion(success)

        # Send alerts
        alert_results = {}
        if success:
            alert_results = alert_manager.send_backup_success(
                backup_id=backup_id,
                stats=stats,
                duration_seconds=duration,
                github_owner=settings.github_owner,
            )
        elif stats["repos"] > 0:
            # Partial success with some errors
            alert_results = alert_manager.send_backup_warning(
                backup_id=backup_id,
                stats=stats,
                duration_seconds=duration,
                warning_messages=error_messages,
                github_owner=settings.github_owner,
            )
        else:
            # All repositories failed
            alert_results = alert_manager.send_backup_error(
                backup_id=backup_id,
                error_message="All repository backups failed",
                stats=stats,
                duration_seconds=duration,
                error_messages=error_messages,
                github_owner=settings.github_owner,
            )

        # Show alert results if any alerts were configured
        if alert_results:
            for channel, sent in alert_results.items():
                if sent:
                    console.print(f"[green]Alert sent via {channel}[/]")
                else:
                    console.print(f"[red]Alert failed via {channel}[/]")

        return success

    except Exception as e:
        print_error("Backup failed", e)
        duration = time.time() - start_time

        # Send error alert
        alert_manager.send_backup_error(
            backup_id=backup_id,
            error_message=str(e),
            stats=stats,
            duration_seconds=duration,
            error_messages=error_messages,
            github_owner=settings.github_owner,
        )

        # Cleanup on error
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
        return False


def main() -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    # Check for CLI mode
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        # Remove 'cli' from argv and run CLI
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from cli import app
        app()
        return 0

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, shutdown_handler.request_shutdown)
    signal.signal(signal.SIGINT, shutdown_handler.request_shutdown)

    try:
        # Load settings
        settings = Settings()

        # Setup logging
        setup_logging(settings.log_level)

        # Validate alerting configuration if enabled
        if settings.alert_enabled:
            alert_mgr = AlertManager(settings)
            config_errors = alert_mgr.get_configuration_errors()
            if config_errors:
                console.print("[yellow]Alerting configuration warnings:[/]")
                for error in config_errors:
                    console.print(f"  [yellow]• {error}[/]")
                console.print()

            if not settings.get_alert_channels():
                console.print(
                    "[yellow]Warning: ALERT_ENABLED=true but no ALERT_CHANNELS configured[/]\n"
                )

        # Check for --now flag (immediate execution)
        if "--now" in sys.argv:
            console.print("[bold]Running backup immediately...[/]\n")
            success = run_backup(settings)
            # Update sync state on successful backup
            if success:
                s3_storage = S3Storage(settings)
                state_manager = SyncStateManager(settings.data_dir, s3_storage)
                state_manager.update_sync_time()
            return 0 if success else 1

        # Start scheduler for continuous operation
        console.print("[bold]Starting GitHub Backup Service[/]\n")
        scheduler = setup_scheduler(settings, lambda: run_backup(settings))
        scheduler.start()

        return 0

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/]")
        return 0

    except Exception as e:
        print_error("Failed to start", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
