"""
GitHub Backup - S3 Client Tests

Tests for S3 storage operations using moto for S3 emulation.
"""

import json
from pathlib import Path

import pytest
from moto import mock_aws

from config import Settings
from storage.s3_client import S3Storage, MultipartUploader


class TestS3Storage:
    """Tests for S3Storage class."""

    @mock_aws
    def test_ensure_bucket_exists_creates_bucket(self, test_settings: Settings):
        """Test that ensure_bucket_exists creates the bucket if it doesn't exist."""
        # Use a fresh bucket name that doesn't exist
        test_settings.s3_bucket = "new-test-bucket"

        storage = S3Storage(test_settings)
        result = storage.ensure_bucket_exists()

        assert result is True

        # Verify bucket was created
        response = storage.s3.list_buckets()
        bucket_names = [b["Name"] for b in response["Buckets"]]
        assert "new-test-bucket" in bucket_names

    @mock_aws
    def test_ensure_bucket_exists_with_existing_bucket(self, test_settings: Settings):
        """Test that ensure_bucket_exists works with existing bucket."""
        storage = S3Storage(test_settings)

        # Create bucket first
        storage.s3.create_bucket(Bucket=test_settings.s3_bucket)

        result = storage.ensure_bucket_exists()
        assert result is True

    @mock_aws
    def test_upload_file(self, test_settings: Settings, temp_dir: Path):
        """Test file upload to S3."""
        storage = S3Storage(test_settings)
        storage.s3.create_bucket(Bucket=test_settings.s3_bucket)

        # Create a test file
        test_file = temp_dir / "test.bundle"
        test_file.write_bytes(b"test content for bundle")

        # Upload
        key = storage.upload_file(test_file, "2024-01-15_02-00-00", "test-repo")

        assert key == "github-backup/2024-01-15_02-00-00/test-repo/test.bundle"

        # Verify upload
        response = storage.s3.get_object(Bucket=test_settings.s3_bucket, Key=key)
        assert response["Body"].read() == b"test content for bundle"

    @mock_aws
    def test_upload_directory(self, test_settings: Settings, temp_dir: Path):
        """Test directory upload to S3."""
        storage = S3Storage(test_settings)
        storage.s3.create_bucket(Bucket=test_settings.s3_bucket)

        # Create test directory structure
        metadata_dir = temp_dir / "metadata"
        metadata_dir.mkdir()
        (metadata_dir / "issues.json").write_text('{"test": true}')
        (metadata_dir / "prs.json").write_text('{"prs": []}')

        # Upload
        count = storage.upload_directory(metadata_dir, "2024-01-15_02-00-00", "test-repo")

        assert count == 2

        # Verify uploads
        response = storage.s3.list_objects_v2(
            Bucket=test_settings.s3_bucket,
            Prefix="github-backup/2024-01-15_02-00-00/test-repo/",
        )
        keys = [obj["Key"] for obj in response.get("Contents", [])]
        assert len(keys) == 2

    @mock_aws
    def test_list_backups(self, test_settings: Settings):
        """Test listing backups from S3."""
        storage = S3Storage(test_settings)
        storage.s3.create_bucket(Bucket=test_settings.s3_bucket)

        # Create some backup folders
        backups = ["2024-01-03_02-00-00", "2024-01-01_02-00-00", "2024-01-02_02-00-00"]
        for backup_id in backups:
            storage.s3.put_object(
                Bucket=test_settings.s3_bucket,
                Key=f"github-backup/{backup_id}/repo/test.bundle",
                Body=b"content",
            )

        # List backups
        result = storage.list_backups()

        # Should be sorted newest first
        assert result == ["2024-01-03_02-00-00", "2024-01-02_02-00-00", "2024-01-01_02-00-00"]

    @mock_aws
    def test_delete_backup(self, test_settings: Settings):
        """Test deleting a backup."""
        storage = S3Storage(test_settings)
        storage.s3.create_bucket(Bucket=test_settings.s3_bucket)

        # Create backup with multiple files
        backup_id = "2024-01-15_02-00-00"
        files = ["repo1/test.bundle", "repo2/test.bundle", "repo1/metadata/issues.json"]
        for f in files:
            storage.s3.put_object(
                Bucket=test_settings.s3_bucket,
                Key=f"github-backup/{backup_id}/{f}",
                Body=b"content",
            )

        # Delete
        deleted = storage.delete_backup(backup_id)

        assert deleted == 3

        # Verify deletion
        response = storage.s3.list_objects_v2(
            Bucket=test_settings.s3_bucket,
            Prefix=f"github-backup/{backup_id}/",
        )
        assert response.get("KeyCount", 0) == 0

    @mock_aws
    def test_cleanup_old_backups_respects_retention(self, test_settings: Settings):
        """Test that cleanup keeps the configured number of backups."""
        test_settings.backup_retention_count = 3
        storage = S3Storage(test_settings)
        storage.s3.create_bucket(Bucket=test_settings.s3_bucket)

        # Create 5 backups
        backups = [
            "2024-01-01_02-00-00",
            "2024-01-02_02-00-00",
            "2024-01-03_02-00-00",
            "2024-01-04_02-00-00",
            "2024-01-05_02-00-00",
        ]
        for backup_id in backups:
            storage.s3.put_object(
                Bucket=test_settings.s3_bucket,
                Key=f"github-backup/{backup_id}/repo/test.bundle",
                Body=b"content",
            )

        # Cleanup
        deleted = storage.cleanup_old_backups()

        assert deleted == 2  # 5 - 3 = 2 deleted

        # Verify remaining backups
        remaining = storage.list_backups()
        assert len(remaining) == 3
        assert remaining == ["2024-01-05_02-00-00", "2024-01-04_02-00-00", "2024-01-03_02-00-00"]

    @mock_aws
    def test_cleanup_preserves_protected_backups(self, test_settings: Settings):
        """Test that cleanup preserves backups that are last for any repo."""
        test_settings.backup_retention_count = 2
        storage = S3Storage(test_settings)
        storage.s3.create_bucket(Bucket=test_settings.s3_bucket)

        # Create 4 backups
        backups = [
            "2024-01-01_02-00-00",
            "2024-01-02_02-00-00",
            "2024-01-03_02-00-00",
            "2024-01-04_02-00-00",
        ]
        for backup_id in backups:
            storage.s3.put_object(
                Bucket=test_settings.s3_bucket,
                Key=f"github-backup/{backup_id}/repo/test.bundle",
                Body=b"content",
            )

        # Protect the oldest backup (simulate dormant repo)
        repo_last_backups = {"dormant-repo": "2024-01-01_02-00-00"}

        # Cleanup with protection
        deleted = storage.cleanup_old_backups(repo_last_backups)

        # Should only delete one (2024-01-02), keeping 2024-01-01 as protected
        assert deleted == 1

        remaining = storage.list_backups()
        assert "2024-01-01_02-00-00" in remaining  # Protected
        assert "2024-01-02_02-00-00" not in remaining  # Deleted

    @mock_aws
    def test_get_backup_size(self, test_settings: Settings):
        """Test calculating backup size."""
        storage = S3Storage(test_settings)
        storage.s3.create_bucket(Bucket=test_settings.s3_bucket)

        backup_id = "2024-01-15_02-00-00"
        # Create files with known sizes
        storage.s3.put_object(
            Bucket=test_settings.s3_bucket,
            Key=f"github-backup/{backup_id}/repo/test.bundle",
            Body=b"x" * 1000,  # 1000 bytes
        )
        storage.s3.put_object(
            Bucket=test_settings.s3_bucket,
            Key=f"github-backup/{backup_id}/repo/metadata.json",
            Body=b"y" * 500,  # 500 bytes
        )

        size = storage.get_backup_size(backup_id)
        assert size == 1500


class TestMultipartUploader:
    """Tests for MultipartUploader class."""

    @mock_aws
    def test_small_file_uses_simple_upload(self, test_settings: Settings, temp_dir: Path):
        """Test that small files use simple upload, not multipart."""
        storage = S3Storage(test_settings)
        storage.s3.create_bucket(Bucket=test_settings.s3_bucket)

        # Create a small file (below threshold)
        small_file = temp_dir / "small.bundle"
        small_file.write_bytes(b"small content")

        # Set high threshold
        uploader = MultipartUploader(
            s3_client=storage.s3,
            bucket=test_settings.s3_bucket,
            chunk_size=5 * 1024 * 1024,
            threshold=100 * 1024 * 1024,  # 100MB threshold
        )

        uploader.upload_file(small_file, "test-key")

        # Verify upload
        response = storage.s3.get_object(Bucket=test_settings.s3_bucket, Key="test-key")
        assert response["Body"].read() == b"small content"

    @mock_aws
    def test_large_file_uses_multipart_upload(self, test_settings: Settings, temp_dir: Path):
        """Test that large files use multipart upload."""
        storage = S3Storage(test_settings)
        storage.s3.create_bucket(Bucket=test_settings.s3_bucket)

        # Create a file larger than threshold
        large_file = temp_dir / "large.bundle"
        # Create ~15KB file (larger than our tiny test threshold)
        large_file.write_bytes(b"x" * 15000)

        # Set very low threshold for testing
        uploader = MultipartUploader(
            s3_client=storage.s3,
            bucket=test_settings.s3_bucket,
            chunk_size=5000,  # 5KB chunks
            threshold=10000,  # 10KB threshold
        )

        uploader.upload_file(large_file, "test-key")

        # Verify upload
        response = storage.s3.get_object(Bucket=test_settings.s3_bucket, Key="test-key")
        content = response["Body"].read()
        assert len(content) == 15000
        assert content == b"x" * 15000
