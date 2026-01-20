"""
GitHub Backup - GitHub Client Module

Provides a wrapper around PyGithub for fetching repositories and their metadata.

Supports two modes:
- Authenticated: With GITHUB_PAT - access to private repos, 5000 requests/hour
- Unauthenticated: Without GITHUB_PAT - public repos only, 60 requests/hour
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Generator, Optional, Union

from github import Github, GithubException
from github.Repository import Repository
from github.Organization import Organization
from github.AuthenticatedUser import AuthenticatedUser
from github.NamedUser import NamedUser

from config import Settings

logger = logging.getLogger(__name__)

# Type alias for owner objects
OwnerType = Union[Organization, AuthenticatedUser, NamedUser]


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
    """Client for interacting with GitHub API for backup operations.

    Supports two modes:
    - Authenticated (with PAT): Private repos, 5000 req/hour, metadata access
    - Unauthenticated (no PAT): Public repos only, 60 req/hour
    """

    def __init__(self, settings: Settings):
        """Initialize the GitHub client.

        Args:
            settings: Application settings containing GitHub credentials.
        """
        self.settings = settings
        self._authenticated = settings.is_authenticated

        if self._authenticated:
            self.gh = Github(settings.github_pat)
            logger.info("GitHub client initialized with authentication (5000 req/hour)")
        else:
            self.gh = Github()  # Unauthenticated
            logger.warning(
                "GitHub client initialized WITHOUT authentication. "
                "Only public repositories accessible (60 req/hour rate limit). "
                "Set GITHUB_PAT for private repos and higher rate limits."
            )

        self._owner: Optional[OwnerType] = None

    @property
    def is_authenticated(self) -> bool:
        """Check if client is running in authenticated mode."""
        return self._authenticated

    @property
    def owner(self) -> OwnerType:
        """Get the owner (organization or user) to backup."""
        if self._owner is None:
            self._owner = self._resolve_owner()
        return self._owner

    def get_rate_limit_info(self) -> dict:
        """Get current rate limit information.

        Returns:
            Dict with 'limit', 'remaining', 'reset' keys.
        """
        rate = self.gh.get_rate_limit()
        return {
            "limit": rate.core.limit,
            "remaining": rate.core.remaining,
            "reset": rate.core.reset.isoformat() if rate.core.reset else None,
        }

    def _resolve_owner(self) -> OwnerType:
        """Resolve the owner name to an Organization or User object.

        Resolution order:
        1. Try as organization
        2. If authenticated and owner matches PAT owner: AuthenticatedUser (private repos)
        3. Fall back to NamedUser (public repos only)
        """
        owner_name = self.settings.github_owner

        # Try as organization first
        try:
            org = self.gh.get_organization(owner_name)
            if self._authenticated:
                logger.info(f"Resolved '{owner_name}' as organization (private repos accessible)")
            else:
                logger.info(f"Resolved '{owner_name}' as organization (public repos only)")
            return org
        except GithubException:
            pass

        # If authenticated, check if owner matches the PAT owner
        if self._authenticated:
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
            if self._authenticated:
                logger.warning(
                    f"Resolved '{owner_name}' as named user (only public repos accessible). "
                    f"For private repos, ensure GITHUB_OWNER matches your PAT owner."
                )
            else:
                logger.info(f"Resolved '{owner_name}' as user (public repos only, no PAT configured)")
            return user
        except GithubException as e:
            raise ValueError(f"Could not find organization or user: {owner_name}") from e

    def get_repositories(self) -> Generator[RepoInfo, None, None]:
        """Get all repositories that should be backed up.

        Behavior depends on GITHUB_BACKUP_ALL_ACCESSIBLE setting:
        - False (default): Only repos owned by GITHUB_OWNER
        - True: All repos the authenticated user has access to (orgs, collaborations, etc.)

        Yields:
            RepoInfo objects matching the backup criteria.
        """
        if isinstance(self.owner, Organization):
            # For organizations, get all repos (public + private if authenticated)
            repos = self.owner.get_repos(type="all")
        elif isinstance(self.owner, AuthenticatedUser):
            if self.settings.github_backup_all_accessible:
                # Get ALL repos the user has access to (owned, collaborator, org member)
                repos = self.owner.get_repos()
                logger.info("Fetching ALL accessible repos (owned + collaborations + org memberships)")
            else:
                # Get ONLY repos owned by this user
                repos = self.owner.get_repos(affiliation="owner")
                logger.info("Fetching only repos owned by authenticated user")
        else:
            # NamedUser - returns public repos of that user
            repos = self.owner.get_repos()

        total_count = 0
        skipped_forks = 0
        skipped_private = 0
        skipped_archived = 0

        for repo in repos:
            total_count += 1
            if self._should_backup(repo):
                yield RepoInfo.from_repo(repo)
            else:
                # Count why repos were skipped
                if repo.fork and not self.settings.github_backup_forks:
                    skipped_forks += 1
                elif repo.private and not self.settings.github_backup_private:
                    skipped_private += 1
                elif repo.archived and not self.settings.github_backup_archived:
                    skipped_archived += 1

        logger.info(
            f"Repository scan complete: {total_count} total, "
            f"skipped {skipped_forks} forks, {skipped_private} private, {skipped_archived} archived"
        )

    def _should_backup(self, repo: Repository) -> bool:
        """Determine if a repository should be included in the backup.

        Args:
            repo: The repository to check.

        Returns:
            True if the repository should be backed up.
        """
        # Skip private repos if not configured
        if repo.private and not self.settings.github_backup_private:
            logger.info(f"Skipping private repo: {repo.full_name}")
            return False

        # Skip forks if not configured
        if repo.fork and not self.settings.github_backup_forks:
            logger.info(f"Skipping fork: {repo.full_name}")
            return False

        # Skip archived if not configured
        if repo.archived and not self.settings.github_backup_archived:
            logger.info(f"Skipping archived repo: {repo.full_name}")
            return False

        return True

    def get_clone_url(self, repo_info: RepoInfo) -> str:
        """Get the clone URL for a repository.

        For authenticated mode, embeds the PAT in the URL for all repos.
        This is necessary because:
        - Private repos require authentication
        - Internal org repos (not public, not private) require authentication
        - Using token for public repos doesn't hurt and increases rate limit

        Args:
            repo_info: Repository information.

        Returns:
            Clone URL (with embedded PAT when authenticated).
        """
        clone_url = repo_info.repo.clone_url

        # Always embed token when authenticated - needed for private repos,
        # internal org repos, and doesn't hurt for public repos
        if self._authenticated:
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

        # Use removesuffix to only replace the trailing .git, not .git in repo names like .github
        clone_url = repo_info.repo.clone_url
        if clone_url.endswith(".git"):
            wiki_url = clone_url[:-4] + ".wiki.git"
        else:
            wiki_url = clone_url + ".wiki.git"

        # Always embed token when authenticated - needed for private repos,
        # internal org repos, and doesn't hurt for public repos
        if self._authenticated:
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
