"""
GitHub Backup - Git Operations Module

Provides git operations for cloning repositories and creating bundles.
Includes Git LFS support for complete backups.
"""

import os
import re
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from git import Repo, GitCommandError

from ui.console import backup_logger

# Environment variable that carries the token to git's credential helper. The
# helper reads it at call time, so the token never appears in a remote URL, on
# a command line, or in a repository's on-disk config.
GIT_TOKEN_ENV_VAR = "GITHUB_BACKUP_GIT_TOKEN"

# Inline credential helper: answers git's "get" request and ignores
# "store"/"erase". "x-access-token" is the username GitHub documents for App
# tokens; for PATs GitHub ignores the username, so it fits every token type.
_CREDENTIAL_HELPER = (
    '!f() { test "$1" = get || return 0; '
    "echo username=x-access-token; "
    f"printf 'password=%s\\n' \"${GIT_TOKEN_ENV_VAR}\"; }}; f"
)


def git_env(token: Optional[str] = None, host: str = "github.com") -> dict[str, str]:
    """Build the environment for git subprocesses.

    Git never prompts. With a token, git authenticates through an inline
    credential helper scoped to ``https://<host>``. The helper is injected via
    ``GIT_CONFIG_*`` environment variables (git >= 2.31), not embedded in the
    remote URL.

    Why not ``https://TOKEN@host/...``: libcurl builds that carry the
    CVE-2026-94266 fix strip credentials from CURLINFO_EFFECTIVE_URL. Git builds
    without the matching compat patch take that as a redirect, rebuild the
    remote URL without the token, and fail the next request with "could not
    read Username". With the helper, the URL carries no credentials, so libcurl
    has nothing to strip and git never sees a false redirect. On real redirects
    (renamed or transferred repositories) git asks the helper again after the
    401. The helper is scoped to the host, so a redirect to any other host
    never receives the token.

    git-lfs only asks for credentials after a 401 by default. Setting
    ``lfs.<url>.access=basic`` makes it authenticate LFS batch requests up
    front, as it did when the token sat in the URL, so public LFS repos keep
    using the authenticated rate limit.

    Args:
        token: GitHub token, or None for anonymous access.
        host: Host the credential helper answers for.

    Returns:
        A full environment dict (os.environ plus git settings).
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if not token:
        return env

    # Append to config entries the caller's environment may already inject
    base = int(env.get("GIT_CONFIG_COUNT") or 0)
    helper_key = f"credential.https://{host}.helper"
    entries = [
        (helper_key, ""),  # an empty value resets helpers from system/global config
        (helper_key, _CREDENTIAL_HELPER),
        (f"lfs.https://{host}.access", "basic"),
    ]
    for offset, (name, value) in enumerate(entries, start=base):
        env[f"GIT_CONFIG_KEY_{offset}"] = name
        env[f"GIT_CONFIG_VALUE_{offset}"] = value
    env["GIT_CONFIG_COUNT"] = str(base + len(entries))
    env[GIT_TOKEN_ENV_VAR] = token
    return env


def mask_credentials(text: str) -> str:
    """Mask embedded credentials in URLs within a string.

    Replaces patterns like https://TOKEN@github.com with https://***@github.com
    to prevent leaking PATs in logs, alerts, or error messages.
    """
    return re.sub(r"https://[^@]+@", "https://***@", str(text))


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

    def __init__(self, work_dir: Path, token: Optional[str] = None):
        """Initialize git backup handler.

        Args:
            work_dir: Working directory for backup operations.
            token: GitHub token for private repositories, LFS and wikis.
                Passed to git through a credential helper (see git_env).
        """
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._env = git_env(token)

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

        Repo.clone_from(
            repo_url,
            str(mirror_path),
            mirror=True,
            env=self._env,
            kill_after_timeout=3600,
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
                env=self._env
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
                env=self._env
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
                env=self._env
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
                env=self._env,
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
