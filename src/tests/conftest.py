"""
GitHub Backup - Test Fixtures

Provides pytest fixtures for mocking S3 and GitHub API.
"""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock

import boto3
import pytest
import responses
from moto import mock_aws

from config import Settings


# ═══════════════════════════════════════════════════════════════════════════════
# Environment Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_settings(temp_dir: Path) -> Settings:
    """Create test settings with mocked values.

    Note: s3_endpoint_url is None so moto can intercept boto3 calls.
    Moto only intercepts calls to standard AWS endpoints, not custom endpoints.
    """
    return Settings(
        # GitHub
        github_owner="test-org",
        github_pat="ghp_test_token_12345",
        github_backup_private=True,
        github_backup_forks=False,
        github_backup_archived=True,
        # Backup
        backup_retention_count=3,
        backup_include_metadata=True,
        backup_include_wiki=True,
        backup_incremental=True,
        # Scheduler (disabled for tests)
        backup_schedule_enabled=False,
        # S3 - endpoint_url=None allows moto to intercept
        s3_endpoint_url=None,
        s3_bucket="test-bucket",
        s3_access_key="testing",
        s3_secret_key="testing",
        s3_region="us-east-1",
        # Alerting (disabled for most tests)
        alert_enabled=False,
        # App
        data_dir=str(temp_dir),
        log_level="DEBUG",
    )


@pytest.fixture
def alert_settings(temp_dir: Path) -> Settings:
    """Create test settings with alerting enabled."""
    return Settings(
        # GitHub
        github_owner="test-org",
        github_pat="ghp_test_token_12345",
        # S3 - endpoint_url=None allows moto to intercept
        s3_endpoint_url=None,
        s3_bucket="test-bucket",
        s3_access_key="testing",
        s3_secret_key="testing",
        # Alerting
        alert_enabled=True,
        alert_level="all",
        alert_channels="webhook,teams",
        webhook_url="https://webhook.example.com/test",
        webhook_secret="test-secret",
        teams_webhook_url="https://teams.webhook.office.com/test",
        # App
        data_dir=str(temp_dir),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# S3 Fixtures (Moto)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def aws_credentials():
    """Set up mock AWS credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def mock_s3(aws_credentials):
    """Create a mocked S3 service."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        # Create test bucket
        s3.create_bucket(Bucket="test-bucket")
        yield s3


@pytest.fixture
def s3_with_backups(mock_s3):
    """S3 with pre-existing backup data.

    Uses owner prefix structure: github-backup/{owner}/{backup_id}/{repo}/
    """
    # Create some backup folders (with owner prefix for multi-tenant support)
    backups = [
        "2024-01-01_02-00-00",
        "2024-01-02_02-00-00",
        "2024-01-03_02-00-00",
        "2024-01-04_02-00-00",
        "2024-01-05_02-00-00",
    ]

    for backup_id in backups:
        # Create a test file in each backup (test-org is the owner from test_settings)
        mock_s3.put_object(
            Bucket="test-bucket",
            Key=f"github-backup/test-org/{backup_id}/test-repo/test-repo.bundle",
            Body=b"fake bundle content",
        )
        mock_s3.put_object(
            Bucket="test-bucket",
            Key=f"github-backup/test-org/{backup_id}/test-repo/metadata/issues.json",
            Body=json.dumps([{"id": 1, "title": "Test Issue"}]).encode(),
        )

    return mock_s3, backups


# ═══════════════════════════════════════════════════════════════════════════════
# GitHub API Fixtures (Responses)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_github_api():
    """Mock GitHub API responses."""
    with responses.RequestsMock() as rsps:
        # Add common API responses
        _setup_github_mocks(rsps)
        yield rsps


def _setup_github_mocks(rsps: responses.RequestsMock):
    """Set up standard GitHub API mock responses."""

    # User/Org check - return as organization
    rsps.add(
        responses.GET,
        "https://api.github.com/orgs/test-org",
        json={
            "login": "test-org",
            "id": 12345,
            "type": "Organization",
        },
        status=200,
    )

    # Repository list
    rsps.add(
        responses.GET,
        "https://api.github.com/orgs/test-org/repos",
        json=_create_mock_repos(),
        status=200,
    )

    # Rate limit
    rsps.add(
        responses.GET,
        "https://api.github.com/rate_limit",
        json={
            "resources": {
                "core": {"limit": 5000, "remaining": 4999, "reset": 1234567890}
            }
        },
        status=200,
    )


def _create_mock_repos() -> list[dict]:
    """Create mock repository data."""
    base_time = datetime(2024, 1, 15, 10, 0, 0)

    return [
        {
            "id": 1,
            "name": "repo-active",
            "full_name": "test-org/repo-active",
            "private": False,
            "fork": False,
            "archived": False,
            "has_wiki": True,
            "clone_url": "https://github.com/test-org/repo-active.git",
            "pushed_at": base_time.isoformat() + "Z",
            "created_at": "2023-01-01T00:00:00Z",
        },
        {
            "id": 2,
            "name": "repo-private",
            "full_name": "test-org/repo-private",
            "private": True,
            "fork": False,
            "archived": False,
            "has_wiki": False,
            "clone_url": "https://github.com/test-org/repo-private.git",
            "pushed_at": base_time.isoformat() + "Z",
            "created_at": "2023-06-01T00:00:00Z",
        },
        {
            "id": 3,
            "name": "repo-archived",
            "full_name": "test-org/repo-archived",
            "private": False,
            "fork": False,
            "archived": True,
            "has_wiki": True,
            "clone_url": "https://github.com/test-org/repo-archived.git",
            "pushed_at": "2023-01-01T00:00:00Z",
            "created_at": "2022-01-01T00:00:00Z",
        },
        {
            "id": 4,
            "name": "repo-fork",
            "full_name": "test-org/repo-fork",
            "private": False,
            "fork": True,
            "archived": False,
            "has_wiki": False,
            "clone_url": "https://github.com/test-org/repo-fork.git",
            "pushed_at": base_time.isoformat() + "Z",
            "created_at": "2023-12-01T00:00:00Z",
        },
    ]


@pytest.fixture
def mock_github_issues():
    """Create mock issue data."""
    return [
        {
            "number": 1,
            "title": "Test Issue 1",
            "body": "Issue body",
            "state": "open",
            "user": {"login": "testuser"},
            "labels": [{"name": "bug"}],
            "assignees": [],
            "milestone": None,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-15T00:00:00Z",
            "closed_at": None,
            "comments": 2,
            "pull_request": None,
        },
        {
            "number": 2,
            "title": "Test Issue 2",
            "body": "Another issue",
            "state": "closed",
            "user": {"login": "testuser2"},
            "labels": [{"name": "enhancement"}],
            "assignees": [{"login": "dev1"}],
            "milestone": {"title": "v1.0"},
            "created_at": "2024-01-02T00:00:00Z",
            "updated_at": "2024-01-10T00:00:00Z",
            "closed_at": "2024-01-10T00:00:00Z",
            "comments": 5,
            "pull_request": None,
        },
    ]


@pytest.fixture
def mock_github_prs():
    """Create mock pull request data."""
    return [
        {
            "number": 10,
            "title": "Feature PR",
            "body": "PR description",
            "state": "merged",
            "user": {"login": "contributor"},
            "labels": [],
            "requested_reviewers": [],
            "base": {"ref": "main"},
            "head": {"ref": "feature-branch"},
            "merged": True,
            "merged_by": {"login": "maintainer"},
            "created_at": "2024-01-05T00:00:00Z",
            "updated_at": "2024-01-07T00:00:00Z",
            "closed_at": "2024-01-07T00:00:00Z",
            "merged_at": "2024-01-07T00:00:00Z",
            "commits": 3,
            "additions": 150,
            "deletions": 20,
            "changed_files": 5,
        },
    ]


@pytest.fixture
def mock_github_releases():
    """Create mock release data."""
    return [
        {
            "tag_name": "v1.0.0",
            "name": "Version 1.0.0",
            "body": "Release notes",
            "author": {"login": "releaser"},
            "draft": False,
            "prerelease": False,
            "created_at": "2024-01-10T00:00:00Z",
            "published_at": "2024-01-10T00:00:00Z",
            "assets": [
                {
                    "name": "app.zip",
                    "size": 1024000,
                    "download_count": 100,
                    "browser_download_url": "https://github.com/test-org/repo/releases/download/v1.0.0/app.zip",
                }
            ],
        },
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Webhook/Alerting Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_webhook():
    """Mock webhook endpoint."""
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST,
            "https://webhook.example.com/test",
            json={"status": "ok"},
            status=200,
        )
        yield rsps


@pytest.fixture
def mock_teams_webhook():
    """Mock Teams webhook endpoint."""
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST,
            "https://teams.webhook.office.com/test",
            body="1",  # Teams returns "1" on success
            status=200,
        )
        yield rsps


# ═══════════════════════════════════════════════════════════════════════════════
# Git Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_git_repo(temp_dir: Path) -> Path:
    """Create a mock git repository for testing."""
    import subprocess

    repo_path = temp_dir / "mock-repo"
    repo_path.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    # Create a file and commit
    (repo_path / "README.md").write_text("# Test Repository\n")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    return repo_path
