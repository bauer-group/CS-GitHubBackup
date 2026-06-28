"""Tests for the GitHub client, focused on its request resilience configuration."""

from backup.github_client import RETRYABLE_STATUSES, _build_retry


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
