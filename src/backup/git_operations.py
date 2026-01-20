"""
GitHub Backup - Git Operations Module

Provides git operations for cloning repositories and creating bundles.
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from git import Repo, GitCommandError

logger = logging.getLogger(__name__)


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

        logger.debug(f"Cloning {repo_name} as mirror...")

        # Clone - let caller handle logging for failures
        Repo.clone_from(
            repo_url,
            str(mirror_path),
            mirror=True,
            env={"GIT_TERMINAL_PROMPT": "0"}
        )

        return mirror_path

    def create_bundle(self, mirror_path: Path) -> Path:
        """Create a portable bundle from a mirror repository.

        Args:
            mirror_path: Path to the mirror repository.

        Returns:
            Path to the created bundle file.

        Raises:
            subprocess.CalledProcessError: If bundle creation fails.
        """
        bundle_path = mirror_path.with_suffix(".bundle")
        repo_name = mirror_path.stem

        logger.debug(f"Creating bundle for {repo_name}...")

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
            logger.error(f"Failed to create bundle for {repo_name}: {e.stderr}")
            raise

        return bundle_path

    def clone_and_bundle(self, repo_url: str, repo_name: str) -> tuple[Path, int]:
        """Clone a repository and create a bundle.

        Args:
            repo_url: URL to clone from.
            repo_name: Name for the repository.

        Returns:
            Tuple of (bundle_path, bundle_size_bytes).
        """
        mirror_path = self.mirror_clone(repo_url, repo_name)
        bundle_path = self.create_bundle(mirror_path)
        bundle_size = bundle_path.stat().st_size

        # Cleanup mirror directory to save space
        shutil.rmtree(mirror_path)

        return bundle_path, bundle_size

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
