"""
GitHub Backup - Wiki Backup Module

Handles backup of repository wikis.
"""

import subprocess
from pathlib import Path
from typing import Optional

from ui.console import backup_logger
from .git_operations import GitBackup, mask_credentials


class WikiBackup:
    """Handles wiki repository backups."""

    def __init__(self, git_backup: GitBackup):
        """Initialize wiki backup handler.

        Args:
            git_backup: GitBackup instance to use for git operations.
        """
        self.git_backup = git_backup

    def backup_wiki(
        self,
        wiki_url: Optional[str],
        repo_name: str
    ) -> tuple[Optional[Path], Optional[int]]:
        """Backup a repository's wiki.

        Args:
            wiki_url: URL of the wiki repository.
            repo_name: Name of the main repository.

        Returns:
            Tuple of (bundle_path, size_bytes) if wiki exists, (None, None) otherwise.
        """
        if not wiki_url:
            backup_logger.debug(f"No wiki URL for {repo_name}")
            return None, None

        wiki_name = f"{repo_name}.wiki"

        try:
            result = self.git_backup.clone_and_bundle(wiki_url, wiki_name)

            if result.is_empty or result.bundle_path is None:
                backup_logger.debug(f"Wiki is empty for {repo_name}")
                return None, None

            backup_logger.info(f"Wiki backup created for {repo_name}")
            return result.bundle_path, result.bundle_size

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            # Wiki might be enabled but empty, or access denied
            error_msg = mask_credentials(e).lower()
            if "repository not found" in error_msg or "not exist" in error_msg:
                backup_logger.debug(f"Wiki not available for {repo_name} (empty or disabled)")
            else:
                backup_logger.warning(f"Failed to backup wiki for {repo_name}: {error_msg}")
            return None, None

        except Exception as e:
            backup_logger.warning(f"Unexpected error backing up wiki for {repo_name}: {mask_credentials(e)}")
            return None, None
