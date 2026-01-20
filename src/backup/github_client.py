"""
GitHub Backup - GitHub Client Module

Provides a wrapper around PyGithub for fetching repositories and their metadata.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Generator, Optional

from github import Github, GithubException
from github.Repository import Repository
from github.Organization import Organization
from github.AuthenticatedUser import AuthenticatedUser

from config import Settings

logger = logging.getLogger(__name__)


@dataclass
class RepoInfo:
    """Repository information for backup processing."""

    repo: Repository
    name: str
    full_name: str
    pushed_at: str  # ISO format string for comparison
    has_wiki: bool
    private: bool

    @classmethod
    def from_repo(cls, repo: Repository) -> "RepoInfo":
        """Create RepoInfo from a PyGithub Repository object."""
        pushed_at_dt = repo.pushed_at
        if pushed_at_dt is None:
            # Use created_at if never pushed
            pushed_at_dt = repo.created_at

        return cls(
            repo=repo,
            name=repo.name,
            full_name=repo.full_name,
            pushed_at=pushed_at_dt.isoformat() if pushed_at_dt else "",
            has_wiki=repo.has_wiki,
            private=repo.private,
        )


class GitHubBackupClient:
    """Client for interacting with GitHub API for backup operations."""

    def __init__(self, settings: Settings):
        """Initialize the GitHub client.

        Args:
            settings: Application settings containing GitHub credentials.
        """
        self.gh = Github(settings.github_pat)
        self.settings = settings
        self._owner: Optional[Organization | AuthenticatedUser] = None

    @property
    def owner(self) -> Organization | AuthenticatedUser:
        """Get the owner (organization or user) to backup."""
        if self._owner is None:
            self._owner = self._resolve_owner()
        return self._owner

    def _resolve_owner(self) -> Organization | AuthenticatedUser:
        """Resolve the owner name to an Organization or User object.

        For private repository access:
        - Organizations: Uses get_repos(type="all") in get_repositories()
        - Users: Must use AuthenticatedUser (get_user() without args)
          NamedUser (get_user(name)) only sees public repositories!
        """
        owner_name = self.settings.github_owner

        # Try as organization first
        try:
            org = self.gh.get_organization(owner_name)
            logger.info(f"Resolved '{owner_name}' as organization")
            return org
        except GithubException:
            pass

        # Check if owner is the authenticated user (required for private repo access)
        try:
            authenticated_user = self.gh.get_user()  # No args = AuthenticatedUser
            if authenticated_user.login.lower() == owner_name.lower():
                logger.info(f"Resolved '{owner_name}' as authenticated user (private repos accessible)")
                return authenticated_user
        except GithubException:
            pass

        # Fall back to named user (only public repos visible)
        try:
            user = self.gh.get_user(owner_name)
            logger.warning(
                f"Resolved '{owner_name}' as named user (only public repos accessible). "
                f"For private repos, ensure GITHUB_OWNER matches your PAT owner."
            )
            return user
        except GithubException as e:
            raise ValueError(f"Could not find organization or user: {owner_name}") from e

    def get_repositories(self) -> Generator[RepoInfo, None, None]:
        """Get all repositories that should be backed up.

        Yields:
            RepoInfo objects matching the backup criteria.
        """
        # For organizations, we need to specify type='all' to include private repos
        # For users, get_repos() already returns all repos the authenticated user can access
        if isinstance(self.owner, Organization):
            repos = self.owner.get_repos(type="all")
        else:
            repos = self.owner.get_repos()

        for repo in repos:
            if self._should_backup(repo):
                yield RepoInfo.from_repo(repo)

    def _should_backup(self, repo: Repository) -> bool:
        """Determine if a repository should be included in the backup.

        Args:
            repo: The repository to check.

        Returns:
            True if the repository should be backed up.
        """
        # Skip private repos if not configured
        if repo.private and not self.settings.github_backup_private:
            logger.debug(f"Skipping private repo: {repo.full_name}")
            return False

        # Skip forks if not configured
        if repo.fork and not self.settings.github_backup_forks:
            logger.debug(f"Skipping fork: {repo.full_name}")
            return False

        # Skip archived if not configured
        if repo.archived and not self.settings.github_backup_archived:
            logger.debug(f"Skipping archived repo: {repo.full_name}")
            return False

        return True

    def get_clone_url(self, repo_info: RepoInfo) -> str:
        """Get the clone URL for a repository with authentication.

        Args:
            repo_info: Repository information.

        Returns:
            Clone URL with embedded PAT for authentication.
        """
        clone_url = repo_info.repo.clone_url
        # Use HTTPS URL with embedded token for private repos
        if repo_info.private:
            return clone_url.replace(
                "https://",
                f"https://{self.settings.github_pat}@"
            )
        return clone_url

    def get_wiki_url(self, repo_info: RepoInfo) -> Optional[str]:
        """Get the wiki clone URL if wiki is enabled.

        Args:
            repo_info: Repository information.

        Returns:
            Wiki clone URL or None if wiki is not enabled.
        """
        if not repo_info.has_wiki:
            return None

        wiki_url = repo_info.repo.clone_url.replace(".git", ".wiki.git")
        if repo_info.private:
            wiki_url = wiki_url.replace(
                "https://",
                f"https://{self.settings.github_pat}@"
            )
        return wiki_url

    def count_repositories(self) -> int:
        """Count the total number of repositories to backup.

        Returns:
            Number of repositories matching backup criteria.
        """
        return sum(1 for _ in self.get_repositories())
