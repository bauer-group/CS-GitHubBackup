"""
GitHub Backup - S3 Storage Module

Provides S3-compatible storage operations for backup upload and retention management.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from config import Settings

logger = logging.getLogger(__name__)


class MultipartUploader:
    """Handles multipart uploads with equal-sized chunks for S3 compatibility."""

    def __init__(
        self,
        s3_client,
        bucket: str,
        chunk_size: int,
        threshold: int,
    ):
        """Initialize multipart uploader.

        Args:
            s3_client: boto3 S3 client.
            bucket: Target bucket name.
            chunk_size: Size of each chunk in bytes (equal for all except last).
            threshold: File size threshold for multipart upload.
        """
        self.s3 = s3_client
        self.bucket = bucket
        self.chunk_size = chunk_size
        self.threshold = threshold

    def upload_file(self, local_path: Path, key: str) -> None:
        """Upload file using multipart upload if above threshold.

        All chunks will be equal size except the last one, as required
        by some S3-compatible servers.

        Args:
            local_path: Path to the local file.
            key: S3 object key.
        """
        file_size = local_path.stat().st_size

        if file_size < self.threshold:
            # Use simple upload for small files
            self.s3.upload_file(str(local_path), self.bucket, key)
            return

        logger.debug(
            f"Using multipart upload for {local_path.name} "
            f"({file_size / (1024*1024):.1f} MB)"
        )

        # Initialize multipart upload
        response = self.s3.create_multipart_upload(Bucket=self.bucket, Key=key)
        upload_id = response["UploadId"]

        parts = []
        part_number = 1

        try:
            with open(local_path, "rb") as f:
                while True:
                    chunk = f.read(self.chunk_size)
                    if not chunk:
                        break

                    # Upload part
                    part_response = self.s3.upload_part(
                        Bucket=self.bucket,
                        Key=key,
                        PartNumber=part_number,
                        UploadId=upload_id,
                        Body=chunk,
                    )

                    parts.append({
                        "PartNumber": part_number,
                        "ETag": part_response["ETag"],
                    })

                    logger.debug(
                        f"Uploaded part {part_number} ({len(chunk) / (1024*1024):.1f} MB)"
                    )
                    part_number += 1

            # Complete multipart upload
            self.s3.complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )

            logger.debug(
                f"Completed multipart upload with {len(parts)} parts"
            )

        except Exception as e:
            # Abort upload on failure
            logger.error(f"Multipart upload failed, aborting: {e}")
            self.s3.abort_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
            )
            raise


class S3Storage:
    """S3-compatible storage client for backup operations."""

    def __init__(self, settings: Settings):
        """Initialize S3 storage client.

        Args:
            settings: Application settings with S3 configuration.
        """
        self.settings = settings
        self.bucket = settings.s3_bucket
        self.retention = settings.backup_retention_count
        self.prefix = "github-backup"

        # Configure boto3 for S3-compatible endpoints
        boto_config = BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},  # Required for MinIO
        )

        self.s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=boto_config,
        )

        # Initialize multipart uploader for large files
        self.uploader = MultipartUploader(
            s3_client=self.s3,
            bucket=self.bucket,
            chunk_size=settings.s3_multipart_chunk_size,
            threshold=settings.s3_multipart_threshold,
        )

    def upload_file(self, local_path: Path, backup_id: str, repo_name: str) -> str:
        """Upload a file to S3.

        Uses multipart upload for large files to ensure equal-sized chunks,
        which is required by some S3-compatible servers.

        Args:
            local_path: Path to the local file.
            backup_id: Backup identifier (timestamp).
            repo_name: Name of the repository.

        Returns:
            S3 key of the uploaded file.
        """
        key = f"{self.prefix}/{backup_id}/{repo_name}/{local_path.name}"

        logger.debug(f"Uploading {local_path.name} to s3://{self.bucket}/{key}")

        try:
            self.uploader.upload_file(local_path, key)
            return key
        except ClientError as e:
            logger.error(f"Failed to upload {local_path}: {e}")
            raise

    def upload_directory(self, local_dir: Path, backup_id: str, repo_name: str) -> int:
        """Upload all files from a directory to S3.

        Args:
            local_dir: Path to the local directory.
            backup_id: Backup identifier (timestamp).
            repo_name: Name of the repository.

        Returns:
            Number of files uploaded.
        """
        count = 0
        for file_path in local_dir.rglob("*"):
            if file_path.is_file():
                relative_path = file_path.relative_to(local_dir)
                key = f"{self.prefix}/{backup_id}/{repo_name}/{relative_path}"

                try:
                    self.s3.upload_file(str(file_path), self.bucket, key)
                    count += 1
                except ClientError as e:
                    logger.warning(f"Failed to upload {file_path}: {e}")

        return count

    def list_backups(self) -> list[str]:
        """List all backup folders in the bucket.

        Returns:
            List of backup IDs (folder names) sorted newest first.
        """
        try:
            response = self.s3.list_objects_v2(
                Bucket=self.bucket,
                Prefix=f"{self.prefix}/",
                Delimiter="/",
            )

            # Extract backup IDs from common prefixes
            prefixes = []
            for prefix_obj in response.get("CommonPrefixes", []):
                prefix = prefix_obj.get("Prefix", "")
                # Extract backup ID from "github-backup/YYYY-MM-DD_HH-MM-SS/"
                parts = prefix.strip("/").split("/")
                if len(parts) >= 2:
                    prefixes.append(parts[1])

            # Sort by date (newest first)
            prefixes.sort(reverse=True)
            return prefixes

        except ClientError as e:
            logger.error(f"Failed to list backups: {e}")
            return []

    def delete_backup(self, backup_id: str) -> int:
        """Delete a backup folder and all its contents.

        Args:
            backup_id: Backup identifier to delete.

        Returns:
            Number of objects deleted.
        """
        prefix = f"{self.prefix}/{backup_id}/"
        deleted_count = 0

        try:
            # List all objects with this prefix
            paginator = self.s3.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self.bucket, Prefix=prefix)

            for page in pages:
                objects = page.get("Contents", [])
                if not objects:
                    continue

                # Delete objects in batches of 1000
                delete_keys = [{"Key": obj["Key"]} for obj in objects]

                response = self.s3.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": delete_keys, "Quiet": True},
                )

                deleted_count += len(delete_keys)
                errors = response.get("Errors", [])
                if errors:
                    for error in errors:
                        logger.warning(f"Failed to delete {error['Key']}: {error['Message']}")

            logger.info(f"Deleted backup {backup_id} ({deleted_count} objects)")
            return deleted_count

        except ClientError as e:
            logger.error(f"Failed to delete backup {backup_id}: {e}")
            return 0

    def cleanup_old_backups(
        self,
        repo_last_backups: Optional[dict[str, str]] = None,
    ) -> int:
        """Remove backups older than the retention count.

        Preserves backups that are the last backup for any repository,
        even if they exceed the retention count. This ensures dormant
        repositories always have at least one backup available.

        Args:
            repo_last_backups: Dict mapping repo names to their last backup IDs.
                These backup IDs will be protected from deletion.

        Returns:
            Number of backups deleted.
        """
        backups = self.list_backups()
        deleted_count = 0

        if len(backups) <= self.retention:
            logger.debug(f"No cleanup needed: {len(backups)} backups <= {self.retention} retention")
            return 0

        # Build set of protected backup IDs (last backup for each repo)
        protected_backup_ids = set()
        if repo_last_backups:
            protected_backup_ids = set(repo_last_backups.values())
            logger.debug(f"Protected backup IDs (last backup for repos): {protected_backup_ids}")

        # Delete oldest backups exceeding retention, but protect repo last backups
        candidates_to_delete = backups[self.retention:]
        to_delete = []

        for backup_id in candidates_to_delete:
            if backup_id in protected_backup_ids:
                logger.info(
                    f"Preserving backup {backup_id} (last backup for one or more repos)"
                )
            else:
                to_delete.append(backup_id)

        if to_delete:
            logger.info(f"Cleaning up {len(to_delete)} old backup(s)")

            for backup_id in to_delete:
                self.delete_backup(backup_id)
                deleted_count += 1

        return deleted_count

    def ensure_bucket_exists(self) -> bool:
        """Ensure the target bucket exists.

        Returns:
            True if bucket exists or was created.
        """
        try:
            self.s3.head_bucket(Bucket=self.bucket)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "404":
                logger.info(f"Bucket {self.bucket} does not exist, creating...")
                try:
                    self.s3.create_bucket(Bucket=self.bucket)
                    return True
                except ClientError as create_error:
                    logger.error(f"Failed to create bucket: {create_error}")
                    return False
            else:
                logger.error(f"Error checking bucket: {e}")
                return False

    def get_backup_size(self, backup_id: str) -> int:
        """Get total size of a backup in bytes.

        Args:
            backup_id: Backup identifier.

        Returns:
            Total size in bytes.
        """
        prefix = f"{self.prefix}/{backup_id}/"
        total_size = 0

        try:
            paginator = self.s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    total_size += obj.get("Size", 0)
        except ClientError:
            pass

        return total_size
