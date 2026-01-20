"""
GitHub Backup - Metadata Exporter Module

Exports repository metadata (issues, pull requests, releases) to JSON.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from github import GithubException
from github.Repository import Repository

from ui.console import backup_logger


class MetadataExporter:
    """Exports repository metadata to JSON files."""

    def __init__(self, output_dir: Path):
        """Initialize the metadata exporter.

        Args:
            output_dir: Directory to write JSON files to.
        """
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_all(self, repo: Repository) -> dict[str, int]:
        """Export all metadata for a repository.

        Args:
            repo: The repository to export metadata from.

        Returns:
            Dictionary with counts of exported items.
        """
        repo_dir = self.output_dir / repo.name / "metadata"
        repo_dir.mkdir(parents=True, exist_ok=True)

        counts = {
            "issues": 0,
            "prs": 0,
            "releases": 0,
        }

        # Export issues
        try:
            issues = self.export_issues(repo, repo_dir / "issues.json")
            counts["issues"] = len(issues)
        except GithubException as e:
            backup_logger.warning(f"Failed to export issues for {repo.name}: {e}")

        # Export pull requests
        try:
            prs = self.export_pull_requests(repo, repo_dir / "pull-requests.json")
            counts["prs"] = len(prs)
        except GithubException as e:
            backup_logger.warning(f"Failed to export PRs for {repo.name}: {e}")

        # Export releases
        try:
            releases = self.export_releases(repo, repo_dir / "releases.json")
            counts["releases"] = len(releases)
        except GithubException as e:
            backup_logger.warning(f"Failed to export releases for {repo.name}: {e}")

        return counts

    def export_issues(self, repo: Repository, output_path: Path) -> list[dict]:
        """Export all issues to a JSON file.

        Args:
            repo: The repository.
            output_path: Path to write the JSON file.

        Returns:
            List of exported issues.
        """
        backup_logger.debug(f"Exporting issues for {repo.name}...")

        issues = []
        for issue in repo.get_issues(state="all"):
            # Skip pull requests (they appear in issues API too)
            if issue.pull_request:
                continue

            issue_data = self._issue_to_dict(issue)
            issues.append(issue_data)

        self._write_json(issues, output_path)
        return issues

    def export_pull_requests(self, repo: Repository, output_path: Path) -> list[dict]:
        """Export all pull requests to a JSON file.

        Args:
            repo: The repository.
            output_path: Path to write the JSON file.

        Returns:
            List of exported pull requests.
        """
        backup_logger.debug(f"Exporting pull requests for {repo.name}...")

        prs = []
        for pr in repo.get_pulls(state="all"):
            pr_data = self._pr_to_dict(pr)
            prs.append(pr_data)

        self._write_json(prs, output_path)
        return prs

    def export_releases(self, repo: Repository, output_path: Path) -> list[dict]:
        """Export all releases to a JSON file.

        Args:
            repo: The repository.
            output_path: Path to write the JSON file.

        Returns:
            List of exported releases.
        """
        backup_logger.debug(f"Exporting releases for {repo.name}...")

        releases = []
        for release in repo.get_releases():
            release_data = self._release_to_dict(release)
            releases.append(release_data)

        self._write_json(releases, output_path)
        return releases

    def _issue_to_dict(self, issue) -> dict[str, Any]:
        """Convert an issue to a dictionary."""
        return {
            "number": issue.number,
            "title": issue.title,
            "body": issue.body,
            "state": issue.state,
            "author": issue.user.login if issue.user else None,
            "labels": [label.name for label in issue.labels],
            "assignees": [a.login for a in issue.assignees],
            "milestone": issue.milestone.title if issue.milestone else None,
            "created_at": self._datetime_to_str(issue.created_at),
            "updated_at": self._datetime_to_str(issue.updated_at),
            "closed_at": self._datetime_to_str(issue.closed_at),
            "comments_count": issue.comments,
            "comments": self._export_issue_comments(issue),
        }

    def _pr_to_dict(self, pr) -> dict[str, Any]:
        """Convert a pull request to a dictionary."""
        return {
            "number": pr.number,
            "title": pr.title,
            "body": pr.body,
            "state": pr.state,
            "author": pr.user.login if pr.user else None,
            "labels": [label.name for label in pr.labels],
            "reviewers": [r.login for r in pr.requested_reviewers],
            "base_branch": pr.base.ref,
            "head_branch": pr.head.ref,
            "merged": pr.merged,
            "merged_by": pr.merged_by.login if pr.merged_by else None,
            "created_at": self._datetime_to_str(pr.created_at),
            "updated_at": self._datetime_to_str(pr.updated_at),
            "closed_at": self._datetime_to_str(pr.closed_at),
            "merged_at": self._datetime_to_str(pr.merged_at),
            "commits_count": pr.commits,
            "additions": pr.additions,
            "deletions": pr.deletions,
            "changed_files": pr.changed_files,
        }

    def _release_to_dict(self, release) -> dict[str, Any]:
        """Convert a release to a dictionary."""
        return {
            "tag_name": release.tag_name,
            "name": release.title,
            "body": release.body,
            "author": release.author.login if release.author else None,
            "draft": release.draft,
            "prerelease": release.prerelease,
            "created_at": self._datetime_to_str(release.created_at),
            "published_at": self._datetime_to_str(release.published_at),
            "assets": [
                {
                    "name": asset.name,
                    "size": asset.size,
                    "download_count": asset.download_count,
                    "download_url": asset.browser_download_url,
                }
                for asset in release.get_assets()
            ],
        }

    def _export_issue_comments(self, issue) -> list[dict]:
        """Export comments for an issue."""
        comments = []
        try:
            for comment in issue.get_comments():
                comments.append({
                    "author": comment.user.login if comment.user else None,
                    "body": comment.body,
                    "created_at": self._datetime_to_str(comment.created_at),
                    "updated_at": self._datetime_to_str(comment.updated_at),
                })
        except GithubException:
            backup_logger.debug(f"Could not fetch comments for issue #{issue.number}")
        return comments

    @staticmethod
    def _datetime_to_str(dt: Optional[datetime]) -> Optional[str]:
        """Convert datetime to ISO format string."""
        return dt.isoformat() if dt else None

    @staticmethod
    def _write_json(data: Any, path: Path) -> None:
        """Write data to a JSON file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
