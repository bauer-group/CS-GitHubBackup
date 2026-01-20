"""
GitHub Backup - Console UI Module

Provides rich console output with progress bars, tables, and formatted logging.
"""

from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.logging import RichHandler
import logging

# Global console instance
console = Console()


def setup_logging(level: str = "INFO") -> None:
    """Configure logging with Rich handler."""
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def create_progress() -> Progress:
    """Create a progress bar for backup operations."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )


def print_banner(backup_id: str) -> None:
    """Print the startup banner with backup ID."""
    banner = Text()
    banner.append("GitHub Backup\n", style="bold cyan")
    banner.append(f"Backup ID: {backup_id}\n", style="dim")
    banner.append(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", style="dim")

    console.print(Panel(banner, title="[bold]Backup Started[/]", border_style="cyan"))


def print_repo_status(
    repo_name: str,
    git_size: Optional[str] = None,
    has_lfs: bool = False,
    lfs_size: Optional[str] = None,
    issues: Optional[int] = None,
    prs: Optional[int] = None,
    releases: Optional[int] = None,
    wiki: Optional[bool] = None,
    error: Optional[str] = None,
) -> None:
    """Print status for a single repository backup."""
    if error:
        console.print(f"  [red]✗[/] {repo_name}: [red]{error}[/]")
        return

    parts = [f"[green]✓[/] {repo_name}"]

    if git_size:
        parts.append(f"[cyan]Git: {git_size}[/]")

    if has_lfs and lfs_size:
        parts.append(f"[bright_cyan]LFS: {lfs_size}[/]")
    else:
        parts.append("[dim]LFS: -[/]")

    if issues is not None:
        parts.append(f"[yellow]Issues: {issues}[/]")

    if prs is not None:
        parts.append(f"[magenta]PRs: {prs}[/]")

    if releases is not None:
        parts.append(f"[blue]Releases: {releases}[/]")

    if wiki is True:
        parts.append("[green]Wiki: ✓[/]")
    elif wiki is False:
        parts.append("[dim]Wiki: -[/]")

    console.print("  " + " | ".join(parts))


def print_summary(stats: dict, duration_seconds: float) -> None:
    """Print the final backup summary table."""
    table = Table(title="Backup Summary", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="cyan", width=20)
    table.add_column("Value", style="green", justify="right")

    # Show backed up and skipped repos
    repos_backed_up = stats.get("repos", 0)
    repos_skipped = stats.get("skipped", 0)

    if repos_skipped > 0:
        table.add_row("Repositories Backed Up", str(repos_backed_up))
        table.add_row("Repositories Skipped", f"[dim]{repos_skipped}[/] [dim](unchanged)[/]")
    else:
        table.add_row("Repositories", str(repos_backed_up))

    # Only show metadata counts if repos were backed up
    if repos_backed_up > 0:
        # Show LFS repos if any
        lfs_repos = stats.get("lfs_repos", 0)
        if lfs_repos > 0:
            table.add_row("Repos with LFS", str(lfs_repos))

        table.add_row("Issues", str(stats.get("issues", 0)))
        table.add_row("Pull Requests", str(stats.get("prs", 0)))
        table.add_row("Releases", str(stats.get("releases", 0)))
        table.add_row("Wikis", str(stats.get("wikis", 0)))

        if "total_size" in stats and stats["total_size"] > 0:
            table.add_row("Total Size", format_size(stats["total_size"]))

    table.add_row("Duration", format_duration(duration_seconds))

    if stats.get("errors", 0) > 0:
        table.add_row("Errors", f"[red]{stats['errors']}[/]")

    if "deleted_backups" in stats:
        table.add_row("Old Backups Removed", str(stats["deleted_backups"]))

    console.print()
    console.print(table)


def print_completion(success: bool = True) -> None:
    """Print completion message."""
    if success:
        console.print("\n[bold green]✓ Backup completed successfully[/]")
    else:
        console.print("\n[bold red]✗ Backup completed with errors[/]")


def print_scheduler_info(schedule_description: str) -> None:
    """Print scheduler information.

    Args:
        schedule_description: Human-readable schedule description.
    """
    console.print(
        Panel(
            f"[green]Scheduler active[/]\n"
            f"Schedule: [bold]{schedule_description}[/]",
            title="[bold]Scheduler[/]",
            border_style="green",
        )
    )


def print_error(message: str, exception: Optional[Exception] = None) -> None:
    """Print an error message."""
    console.print(f"[bold red]Error:[/] {message}")
    if exception:
        console.print(f"[dim]{type(exception).__name__}: {exception}[/]")


def format_size(size_bytes: int) -> str:
    """Format bytes to human-readable size."""
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def format_duration(seconds: float) -> str:
    """Format seconds to human-readable duration."""
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
