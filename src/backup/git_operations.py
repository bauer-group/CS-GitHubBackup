"""
GitHub Backup - Git Operations Module

Provides git operations for cloning repositories and creating bundles.
Includes Git LFS support for complete backups.
"""

import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from git import Repo, GitCommandError

from ui.console import backup_logger


@dataclass
class BackupResult:
    """Result of a repository backup operation."""

    bundle_path: Optional[Path] = None
    bundle_size: int = 0
    lfs_path: Optional[Path] = None
    lfs_size: int = 0
    is_empty: bool = False
    has_lfs: bool = False

    @property
    def total_size(self) -> int:
        """Total size of all backup files."""
        return self.bundle_size + self.lfs_size


class GitBackup:
    """Handles git operations for backup."""

    def __init__(self, work_dir: Path):
        """Initialize git backup handler.

        Args:
            work_dir: Working directory for backup operations.
        """
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def mirror_clone(self, repo_url: str, repo_name: str) -> Path:
        """Clone a repository as a mirror.

        Args:
            repo_url: URL to clone from.
            repo_name: Name for the local clone.

        Returns:
            Path to the cloned mirror repository.

        Raises:
            GitCommandError: If cloning fails.
        """
        mirror_path = self.work_dir / f"{repo_name}.git"

        # Remove existing directory if present
        if mirror_path.exists():
            shutil.rmtree(mirror_path)

        backup_logger.debug(f"Cloning {repo_name} as mirror...")

        # Clone - let caller handle logging for failures
        Repo.clone_from(
            repo_url,
            str(mirror_path),
            mirror=True,
            env={"GIT_TERMINAL_PROMPT": "0"}
        )

        return mirror_path

    def is_empty_repo(self, mirror_path: Path) -> bool:
        """Check if a repository is empty (has no commits).

        Args:
            mirror_path: Path to the mirror repository.

        Returns:
            True if the repository is empty, False otherwise.
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(mirror_path),
                capture_output=True,
                text=True,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            )
            return result.returncode != 0
        except Exception:
            return True

    def create_bundle(self, mirror_path: Path) -> Optional[Path]:
        """Create a portable bundle from a mirror repository.

        Args:
            mirror_path: Path to the mirror repository.

        Returns:
            Path to the created bundle file, or None if repository is empty.

        Raises:
            subprocess.CalledProcessError: If bundle creation fails (except for empty repos).
        """
        bundle_path = mirror_path.with_suffix(".bundle")
        repo_name = mirror_path.stem

        # Check if repository is empty
        if self.is_empty_repo(mirror_path):
            backup_logger.debug(f"Repository {repo_name} is empty, skipping bundle creation")
            return None

        backup_logger.debug(f"Creating bundle for {repo_name}...")

        # Remove existing bundle if present
        if bundle_path.exists():
            bundle_path.unlink()

        try:
            result = subprocess.run(
                ["git", "bundle", "create", str(bundle_path), "--all"],
                cwd=str(mirror_path),
                capture_output=True,
                text=True,
                check=True,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            )
        except subprocess.CalledProcessError as e:
            # Double-check for empty bundle error
            if "empty bundle" in e.stderr.lower():
                backup_logger.debug(f"Repository {repo_name} is empty, skipping bundle creation")
                return None
            backup_logger.debug(f"Failed to create bundle for {repo_name}: {e.stderr}")
            raise

        return bundle_path

    def has_lfs(self, mirror_path: Path) -> bool:
        """Check if a repository uses Git LFS.

        Args:
            mirror_path: Path to the mirror repository.

        Returns:
            True if the repository uses LFS.
        """
        try:
            # Check if git-lfs is installed
            result = subprocess.run(
                ["git", "lfs", "version"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                backup_logger.debug("git-lfs not installed, skipping LFS check")
                return False

            # Check for LFS objects in the repo by listing LFS files
            result = subprocess.run(
                ["git", "lfs", "ls-files", "--all"],
                cwd=str(mirror_path),
                capture_output=True,
                text=True,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            )

            # If there's output, repo has LFS files
            has_lfs_files = bool(result.stdout.strip())
            if has_lfs_files:
                backup_logger.debug(f"Repository uses LFS ({len(result.stdout.strip().splitlines())} files)")
            return has_lfs_files

        except Exception as e:
            backup_logger.debug(f"LFS check failed: {e}")
            return False

    def fetch_lfs_objects(self, mirror_path: Path) -> bool:
        """Fetch all LFS objects for a repository.

        Args:
            mirror_path: Path to the mirror repository.

        Returns:
            True if LFS fetch succeeded.
        """
        repo_name = mirror_path.stem
        backup_logger.debug(f"Fetching LFS objects for {repo_name}...")

        try:
            result = subprocess.run(
                ["git", "lfs", "fetch", "--all"],
                cwd=str(mirror_path),
                capture_output=True,
                text=True,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                timeout=3600,  # 1 hour timeout for large LFS repos
            )

            if result.returncode != 0:
                backup_logger.debug(f"LFS fetch returned non-zero: {result.stderr}")
                # Continue anyway - partial LFS is better than none

            return True

        except subprocess.TimeoutExpired:
            backup_logger.debug(f"LFS fetch timed out for {repo_name}")
            return False
        except Exception as e:
            backup_logger.debug(f"LFS fetch failed for {repo_name}: {e}")
            return False

    def create_lfs_archive(self, mirror_path: Path) -> Optional[Path]:
        """Create a tar.gz archive of LFS objects.

        Args:
            mirror_path: Path to the mirror repository.

        Returns:
            Path to the LFS archive, or None if no LFS objects.
        """
        repo_name = mirror_path.stem
        lfs_objects_dir = mirror_path / "lfs" / "objects"

        # Check if LFS objects directory exists and has content
        if not lfs_objects_dir.exists():
            backup_logger.debug(f"No LFS objects directory for {repo_name}")
            return None

        # Check if there are actual objects
        lfs_files = list(lfs_objects_dir.rglob("*"))
        lfs_files = [f for f in lfs_files if f.is_file()]

        if not lfs_files:
            backup_logger.debug(f"LFS objects directory is empty for {repo_name}")
            return None

        archive_path = mirror_path.with_suffix(".lfs.tar.gz")
        backup_logger.debug(f"Creating LFS archive for {repo_name} ({len(lfs_files)} objects)...")

        try:
            with tarfile.open(archive_path, "w:gz") as tar:
                # Add the lfs/objects directory with relative path
                tar.add(
                    lfs_objects_dir,
                    arcname="lfs/objects",
                    recursive=True
                )

            backup_logger.debug(f"Created LFS archive: {archive_path.name} ({archive_path.stat().st_size} bytes)")
            return archive_path

        except Exception as e:
            backup_logger.debug(f"Failed to create LFS archive for {repo_name}: {e}")
            if archive_path.exists():
                archive_path.unlink()
            return None

    def clone_and_bundle(self, repo_url: str, repo_name: str) -> BackupResult:
        """Clone a repository and create backup files (bundle + LFS if applicable).

        Args:
            repo_url: URL to clone from.
            repo_name: Name for the repository.

        Returns:
            BackupResult with paths and sizes of backup files.
        """
        result = BackupResult()

        # Clone repository
        mirror_path = self.mirror_clone(repo_url, repo_name)

        # Check if empty
        if self.is_empty_repo(mirror_path):
            result.is_empty = True
            shutil.rmtree(mirror_path)
            return result

        # Create git bundle
        bundle_path = self.create_bundle(mirror_path)
        if bundle_path:
            result.bundle_path = bundle_path
            result.bundle_size = bundle_path.stat().st_size

        # Check for and handle LFS
        if self.has_lfs(mirror_path):
            result.has_lfs = True
            backup_logger.debug(f"Repository {repo_name} uses Git LFS, fetching objects...")

            if self.fetch_lfs_objects(mirror_path):
                lfs_archive = self.create_lfs_archive(mirror_path)
                if lfs_archive:
                    result.lfs_path = lfs_archive
                    result.lfs_size = lfs_archive.stat().st_size

        # Cleanup mirror directory to save space
        shutil.rmtree(mirror_path)

        return result

    def get_bundle_size(self, bundle_path: Path) -> int:
        """Get the size of a bundle file in bytes.

        Args:
            bundle_path: Path to the bundle file.

        Returns:
            Size in bytes.
        """
        return bundle_path.stat().st_size

    def cleanup(self) -> None:
        """Remove all temporary files in the working directory."""
        for item in self.work_dir.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)


class WikiBackupError(Exception):
    """Exception raised when wiki backup fails."""
    pass
