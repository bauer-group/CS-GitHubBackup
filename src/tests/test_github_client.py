"""Tests for the GitHub client, focused on its request resilience configuration."""

from unittest.mock import MagicMock

from backup.github_client import (
    RETRYABLE_STATUSES,
    GitHubBackupClient,
    RepoInfo,
    _build_retry,
)
from config import Settings


class TestRetryPolicy:
    """Verify the HTTP retry policy used for all GitHub API requests.

    Regression: GitHub intermittently returns 401 "Bad credentials" on a valid
    token (and 403 for secondary rate limits). Before the fix these were not
    retried, so a single transient failure silently dropped a release's metadata
    from the export. The retry policy must cover them.
    """

    def test_retries_transient_auth_and_rate_limit_statuses(self) -> None:
        retry = _build_retry()
        forced = set(retry.status_forcelist)

        # The transient responses that previously caused dropped metadata.
        assert 401 in forced
        assert 403 in forced

        # The original transient server errors must remain covered.
        for status in (500, 502, 503, 504):
            assert status in forced

    def test_retry_is_bounded_with_backoff(self) -> None:
        retry = _build_retry()

        assert retry.total == 3
        assert retry.backoff_factor == 1
        assert retry.respect_retry_after_header is True

    def test_retryable_statuses_constant_matches_built_policy(self) -> None:
        assert set(_build_retry().status_forcelist) == set(RETRYABLE_STATUSES)


class TestCloneUrls:
    """Clone URLs must never carry the token.

    Regression: tokens embedded as `https://TOKEN@github.com/...` were dropped
    by git once libcurl started stripping credentials from the effective URL
    (CVE-2026-94266 fix), failing private clones with "could not read
    Username". Authentication now goes through git_env's credential helper.
    """

    @staticmethod
    def _repo_info(has_wiki: bool = True) -> RepoInfo:
        repo = MagicMock()
        repo.clone_url = "https://github.com/test-org/.github.git"
        return RepoInfo(
            repo=repo,
            name=".github",
            full_name="test-org/.github",
            pushed_at="",
            has_wiki=has_wiki,
            private=True,
        )

    def test_clone_url_has_no_token(self, test_settings: Settings) -> None:
        client = GitHubBackupClient(test_settings)
        assert client.is_authenticated

        url = client.get_clone_url(self._repo_info())

        assert url == "https://github.com/test-org/.github.git"
        assert test_settings.github_pat not in url

    def test_wiki_url_has_no_token(self, test_settings: Settings) -> None:
        client = GitHubBackupClient(test_settings)

        url = client.get_wiki_url(self._repo_info())

        assert url == "https://github.com/test-org/.github.wiki.git"
        assert test_settings.github_pat not in url

    def test_wiki_url_none_without_wiki(self, test_settings: Settings) -> None:
        client = GitHubBackupClient(test_settings)
        assert client.get_wiki_url(self._repo_info(has_wiki=False)) is None
