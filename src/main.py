#!/usr/bin/env python3
"""
GitHub Backup - Main Entry Point

Automated backup of GitHub repositories to S3-compatible storage.
"""

import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from config import Settings
from scheduler import setup_scheduler
from sync_state_manager import SyncStateManager
from alerting.manager import AlertManager
from backup.github_client import GitHubBackupClient
from backup.git_operations import GitBackup
from backup.metadata_exporter import MetadataExporter
from backup.wiki_backup import WikiBackup
from storage.s3_client import S3Storage
from ui.console import (
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

        # Process repositories with progress bar
        with create_progress() as progress:
            task = progress.add_task("Backing up repositories", total=len(repos_to_backup))

            for repo_info in repos_to_backup:
                repo_name = repo_info.name
                repo_stats = {
                    "git_size": None,
                    "issues": None,
                    "prs": None,
                    "releases": None,
                    "wiki": None,
                    "error": None,
                }

                try:
                    # Backup git repository
                    clone_url = gh_client.get_clone_url(repo_info)
                    bundle_path, bundle_size = git_backup.clone_and_bundle(
                        clone_url, repo_name
                    )
                    repo_stats["git_size"] = format_size(bundle_size)
                    stats["total_size"] += bundle_size

                    # Upload bundle to S3
                    s3_storage.upload_file(bundle_path, backup_id, repo_name)

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
                    logger.error(f"Failed to backup {repo_name}: {e}")
                    repo_stats["error"] = str(e)
                    stats["errors"] += 1
                    error_messages.append(f"{repo_name}: {e}")

                # Print status for this repo
                print_repo_status(
                    repo_name,
                    git_size=repo_stats["git_size"],
                    issues=repo_stats["issues"],
                    prs=repo_stats["prs"],
                    releases=repo_stats["releases"],
                    wiki=repo_stats["wiki"],
                    error=repo_stats["error"],
                )

                progress.advance(task)

        # Cleanup old backups (smart retention - preserves last backup per repo)
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
        print_summary(stats, duration)

        # Print completion status
        success = stats["errors"] == 0
        print_completion(success)

        # Send alerts
        if success:
            alert_manager.send_backup_success(
                backup_id=backup_id,
                stats=stats,
                duration_seconds=duration,
                github_owner=settings.github_owner,
            )
        elif stats["repos"] > 0:
            # Partial success with some errors
            alert_manager.send_backup_warning(
                backup_id=backup_id,
                stats=stats,
                duration_seconds=duration,
                warning_messages=error_messages,
                github_owner=settings.github_owner,
            )
        else:
            # All repositories failed
            alert_manager.send_backup_error(
                backup_id=backup_id,
                error_message="All repository backups failed",
                stats=stats,
                duration_seconds=duration,
                error_messages=error_messages,
                github_owner=settings.github_owner,
            )

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
                state_manager = SyncStateManager(settings.data_dir)
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
