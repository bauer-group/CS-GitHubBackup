"""
GitHub Backup - CLI Module

Command-line interface for backup management and restore operations.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Confirm

from config import Settings
from storage.s3_client import S3Storage
from ui.console import format_size

# ═══════════════════════════════════════════════════════════════════════════════
# CLI App
# ═══════════════════════════════════════════════════════════════════════════════

app = typer.Typer(
    name="github-backup",
    help="GitHub Backup CLI - Manage backups and restore repositories",
    no_args_is_help=True,
)

console = Console()


# ───────────────────────────────────────────────────────────────────────────────
# List Command
# ───────────────────────────────────────────────────────────────────────────────
@app.command("list")
def list_backups():
    """List all available backups."""
    settings = Settings()
    s3 = S3Storage(settings)

    backups = s3.list_backups()

    if not backups:
        console.print("[yellow]No backups found.[/]")
        return

    table = Table(title="Available Backups", show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=4)
    table.add_column("Backup ID", style="cyan")
    table.add_column("Size", justify="right", style="green")
    table.add_column("Repositories", justify="right")

    for idx, backup_id in enumerate(backups, 1):
        size = s3.get_backup_size(backup_id)
        repos = _count_repos_in_backup(s3, backup_id)
        table.add_row(str(idx), backup_id, format_size(size), str(repos))

    console.print(table)


# ───────────────────────────────────────────────────────────────────────────────
# Show Command
# ───────────────────────────────────────────────────────────────────────────────
@app.command("show")
def show_backup(backup_id: str = typer.Argument(..., help="Backup ID to show details for")):
    """Show details of a specific backup."""
    settings = Settings()
    s3 = S3Storage(settings)

    repos = _list_repos_in_backup(s3, backup_id)

    if not repos:
        console.print(f"[red]Backup '{backup_id}' not found or empty.[/]")
        raise typer.Exit(1)

    table = Table(title=f"Backup: {backup_id}", show_header=True, header_style="bold cyan")
    table.add_column("Repository", style="cyan")
    table.add_column("Git Bundle", justify="center")
    table.add_column("Wiki", justify="center")
    table.add_column("Metadata", justify="center")

    for repo in repos:
        has_git = "✓" if repo.get("has_bundle") else "-"
        has_wiki = "✓" if repo.get("has_wiki") else "-"
        has_meta = "✓" if repo.get("has_metadata") else "-"
        table.add_row(repo["name"], has_git, has_wiki, has_meta)

    console.print(table)


# ───────────────────────────────────────────────────────────────────────────────
# Delete Command
# ───────────────────────────────────────────────────────────────────────────────
@app.command("delete")
def delete_backup(
    backup_id: str = typer.Argument(..., help="Backup ID to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete a backup from S3 storage."""
    settings = Settings()
    s3 = S3Storage(settings)

    # Check if backup exists
    backups = s3.list_backups()
    if backup_id not in backups:
        console.print(f"[red]Backup '{backup_id}' not found.[/]")
        raise typer.Exit(1)

    # Confirm deletion
    if not force:
        size = s3.get_backup_size(backup_id)
        console.print(f"\nBackup: [cyan]{backup_id}[/]")
        console.print(f"Size: [green]{format_size(size)}[/]")
        if not Confirm.ask("\n[yellow]Delete this backup?[/]"):
            console.print("[dim]Cancelled.[/]")
            return

    # Delete
    deleted = s3.delete_backup(backup_id)
    console.print(f"[green]✓ Deleted backup '{backup_id}' ({deleted} objects)[/]")


# ───────────────────────────────────────────────────────────────────────────────
# Restore Commands
# ───────────────────────────────────────────────────────────────────────────────
restore_app = typer.Typer(help="Restore repositories from backup")
app.add_typer(restore_app, name="restore")


@restore_app.command("local")
def restore_local(
    backup_id: str = typer.Argument(..., help="Backup ID"),
    repo_name: str = typer.Argument(..., help="Repository name"),
    output_path: Path = typer.Argument(..., help="Output directory path"),
    include_wiki: bool = typer.Option(False, "--wiki", "-w", help="Also restore wiki"),
):
    """Restore a repository to a local directory."""
    settings = Settings()
    s3 = S3Storage(settings)

    output_path = output_path.resolve()

    # Download and restore
    console.print(Panel(f"Restoring [cyan]{repo_name}[/] to [green]{output_path}[/]"))

    with console.status("Downloading bundle..."):
        bundle_path = _download_bundle(s3, backup_id, repo_name, settings)

    if not bundle_path:
        console.print(f"[red]Bundle for '{repo_name}' not found in backup.[/]")
        raise typer.Exit(1)

    # Clone from bundle
    console.print("Restoring from bundle...")
    _clone_from_bundle(bundle_path, output_path)

    # Cleanup
    bundle_path.unlink()

    console.print(f"[green]✓ Repository restored to {output_path}[/]")

    # Restore wiki if requested
    if include_wiki:
        wiki_path = output_path.parent / f"{repo_name}.wiki"
        wiki_bundle = _download_wiki_bundle(s3, backup_id, repo_name, settings)
        if wiki_bundle:
            _clone_from_bundle(wiki_bundle, wiki_path)
            wiki_bundle.unlink()
            console.print(f"[green]✓ Wiki restored to {wiki_path}[/]")
        else:
            console.print("[yellow]Wiki not found in backup.[/]")


@restore_app.command("github")
def restore_github(
    backup_id: str = typer.Argument(..., help="Backup ID"),
    repo_name: str = typer.Argument(..., help="Repository name from backup"),
    target_repo: Optional[str] = typer.Option(
        None, "--target", "-t",
        help="Target repository (owner/repo). Defaults to original."
    ),
    include_wiki: bool = typer.Option(False, "--wiki", "-w", help="Also restore wiki"),
    force: bool = typer.Option(False, "--force", "-f", help="Force push (overwrites remote)"),
):
    """Restore a repository to GitHub."""
    settings = Settings()
    s3 = S3Storage(settings)

    if target_repo is None:
        target_repo = f"{settings.github_owner}/{repo_name}"

    console.print(Panel(
        f"Restoring [cyan]{repo_name}[/] to [green]{target_repo}[/]"
        + ("\n[yellow]WARNING: Force push enabled![/]" if force else "")
    ))

    if force and not Confirm.ask("[yellow]This will overwrite the remote repository. Continue?[/]"):
        console.print("[dim]Cancelled.[/]")
        return

    # Download bundle
    with console.status("Downloading bundle..."):
        bundle_path = _download_bundle(s3, backup_id, repo_name, settings)

    if not bundle_path:
        console.print(f"[red]Bundle for '{repo_name}' not found.[/]")
        raise typer.Exit(1)

    # Create temp directory for clone
    temp_dir = Path(settings.data_dir) / "restore_temp" / repo_name
    temp_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Clone from bundle
        _clone_from_bundle(bundle_path, temp_dir)

        # Set remote
        remote_url = f"https://{settings.github_pat}@github.com/{target_repo}.git"
        subprocess.run(
            ["git", "remote", "set-url", "origin", remote_url],
            cwd=temp_dir, check=True, capture_output=True
        )

        # Push
        push_args = ["git", "push", "--mirror" if force else "--all", "origin"]
        result = subprocess.run(push_args, cwd=temp_dir, capture_output=True, text=True)

        if result.returncode != 0:
            console.print(f"[red]Push failed: {result.stderr}[/]")
            raise typer.Exit(1)

        console.print(f"[green]✓ Repository restored to {target_repo}[/]")

    finally:
        # Cleanup
        bundle_path.unlink(missing_ok=True)
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

    # Restore wiki
    if include_wiki:
        _restore_wiki_to_github(s3, backup_id, repo_name, target_repo, settings, force)


@restore_app.command("git")
def restore_git(
    backup_id: str = typer.Argument(..., help="Backup ID"),
    repo_name: str = typer.Argument(..., help="Repository name from backup"),
    remote_url: str = typer.Argument(..., help="Target Git remote URL"),
    force: bool = typer.Option(False, "--force", "-f", help="Force push (overwrites remote)"),
):
    """Restore a repository to any Git remote."""
    settings = Settings()
    s3 = S3Storage(settings)

    console.print(Panel(
        f"Restoring [cyan]{repo_name}[/] to [green]{remote_url}[/]"
        + ("\n[yellow]WARNING: Force push enabled![/]" if force else "")
    ))

    if force and not Confirm.ask("[yellow]This will overwrite the remote. Continue?[/]"):
        console.print("[dim]Cancelled.[/]")
        return

    # Download bundle
    with console.status("Downloading bundle..."):
        bundle_path = _download_bundle(s3, backup_id, repo_name, settings)

    if not bundle_path:
        console.print(f"[red]Bundle for '{repo_name}' not found.[/]")
        raise typer.Exit(1)

    temp_dir = Path(settings.data_dir) / "restore_temp" / repo_name
    temp_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        _clone_from_bundle(bundle_path, temp_dir)

        subprocess.run(
            ["git", "remote", "set-url", "origin", remote_url],
            cwd=temp_dir, check=True, capture_output=True
        )

        push_args = ["git", "push", "--mirror" if force else "--all", "origin"]
        result = subprocess.run(push_args, cwd=temp_dir, capture_output=True, text=True)

        if result.returncode != 0:
            console.print(f"[red]Push failed: {result.stderr}[/]")
            raise typer.Exit(1)

        console.print(f"[green]✓ Repository pushed to {remote_url}[/]")

    finally:
        bundle_path.unlink(missing_ok=True)
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


# ───────────────────────────────────────────────────────────────────────────────
# Download Command
# ───────────────────────────────────────────────────────────────────────────────
@app.command("download")
def download_backup(
    backup_id: str = typer.Argument(..., help="Backup ID"),
    output_path: Path = typer.Argument(..., help="Output directory"),
    repo_name: Optional[str] = typer.Option(None, "--repo", "-r", help="Specific repo only"),
):
    """Download backup files from S3 to local directory."""
    settings = Settings()
    s3 = S3Storage(settings)

    output_path = output_path.resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    console.print(f"Downloading backup [cyan]{backup_id}[/] to [green]{output_path}[/]")

    prefix = f"{s3.prefix}/{backup_id}/"
    if repo_name:
        prefix += f"{repo_name}/"

    # List and download all objects
    paginator = s3.s3.get_paginator("list_objects_v2")
    total_files = 0
    total_size = 0

    with console.status("Downloading...") as status:
        for page in paginator.paginate(Bucket=s3.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel_path = key.replace(f"{s3.prefix}/{backup_id}/", "")
                local_path = output_path / rel_path

                local_path.parent.mkdir(parents=True, exist_ok=True)
                s3.s3.download_file(s3.bucket, key, str(local_path))

                total_files += 1
                total_size += obj.get("Size", 0)
                status.update(f"Downloaded {total_files} files ({format_size(total_size)})")

    console.print(f"[green]✓ Downloaded {total_files} files ({format_size(total_size)})[/]")


# ───────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ───────────────────────────────────────────────────────────────────────────────
def _count_repos_in_backup(s3: S3Storage, backup_id: str) -> int:
    """Count repositories in a backup."""
    prefix = f"{s3.prefix}/{backup_id}/"
    response = s3.s3.list_objects_v2(
        Bucket=s3.bucket, Prefix=prefix, Delimiter="/"
    )
    return len(response.get("CommonPrefixes", []))


def _list_repos_in_backup(s3: S3Storage, backup_id: str) -> list[dict]:
    """List repositories in a backup with their contents."""
    prefix = f"{s3.prefix}/{backup_id}/"
    response = s3.s3.list_objects_v2(
        Bucket=s3.bucket, Prefix=prefix, Delimiter="/"
    )

    repos = []
    for prefix_obj in response.get("CommonPrefixes", []):
        repo_prefix = prefix_obj["Prefix"]
        repo_name = repo_prefix.rstrip("/").split("/")[-1]

        # Check what files exist
        repo_contents = s3.s3.list_objects_v2(Bucket=s3.bucket, Prefix=repo_prefix)
        files = [obj["Key"] for obj in repo_contents.get("Contents", [])]

        repos.append({
            "name": repo_name,
            "has_bundle": any(f.endswith(".bundle") and "wiki" not in f for f in files),
            "has_wiki": any("wiki.bundle" in f for f in files),
            "has_metadata": any("metadata/" in f for f in files),
        })

    return repos


def _download_bundle(s3: S3Storage, backup_id: str, repo_name: str, settings: Settings) -> Optional[Path]:
    """Download a repository bundle from S3."""
    key = f"{s3.prefix}/{backup_id}/{repo_name}/{repo_name}.bundle"
    local_path = Path(settings.data_dir) / "temp" / f"{repo_name}.bundle"
    local_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        s3.s3.download_file(s3.bucket, key, str(local_path))
        return local_path
    except Exception:
        return None


def _download_wiki_bundle(s3: S3Storage, backup_id: str, repo_name: str, settings: Settings) -> Optional[Path]:
    """Download a wiki bundle from S3."""
    key = f"{s3.prefix}/{backup_id}/{repo_name}/{repo_name}.wiki.bundle"
    local_path = Path(settings.data_dir) / "temp" / f"{repo_name}.wiki.bundle"
    local_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        s3.s3.download_file(s3.bucket, key, str(local_path))
        return local_path
    except Exception:
        return None


def _clone_from_bundle(bundle_path: Path, output_path: Path) -> None:
    """Clone a repository from a bundle file."""
    if output_path.exists():
        shutil.rmtree(output_path)

    subprocess.run(
        ["git", "clone", str(bundle_path), str(output_path)],
        check=True, capture_output=True
    )


def _restore_wiki_to_github(
    s3: S3Storage,
    backup_id: str,
    repo_name: str,
    target_repo: str,
    settings: Settings,
    force: bool
) -> None:
    """Restore wiki to GitHub."""
    wiki_bundle = _download_wiki_bundle(s3, backup_id, repo_name, settings)
    if not wiki_bundle:
        console.print("[yellow]Wiki not found in backup.[/]")
        return

    temp_dir = Path(settings.data_dir) / "restore_temp" / f"{repo_name}.wiki"

    try:
        _clone_from_bundle(wiki_bundle, temp_dir)

        remote_url = f"https://{settings.github_pat}@github.com/{target_repo}.wiki.git"
        subprocess.run(
            ["git", "remote", "set-url", "origin", remote_url],
            cwd=temp_dir, check=True, capture_output=True
        )

        push_args = ["git", "push", "--mirror" if force else "--all", "origin"]
        result = subprocess.run(push_args, cwd=temp_dir, capture_output=True, text=True)

        if result.returncode == 0:
            console.print(f"[green]✓ Wiki restored to {target_repo}.wiki[/]")
        else:
            console.print(f"[yellow]Wiki push failed: {result.stderr}[/]")

    finally:
        wiki_bundle.unlink(missing_ok=True)
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


# ═══════════════════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app()
