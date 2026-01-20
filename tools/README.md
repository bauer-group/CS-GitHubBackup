# GitHub Backup - Tools

Helper scripts for setup and administration.

## Prerequisites

1. **MinIO Client (mc)** must be installed:
   - Windows: `winget install minio.mc` or download from https://min.io/download
   - Linux: `curl https://dl.min.io/client/mc/release/linux-amd64/mc -o /usr/local/bin/mc && chmod +x /usr/local/bin/mc`
   - macOS: `brew install minio/stable/mc`

2. **Python dependencies**:
   ```bash
   pip install -r tools/requirements.txt
   ```

## setup-bucket.py

Creates and configures a MinIO bucket with proper IAM setup for the backup application.

### What it creates

| Resource | Default Name | Purpose |
|----------|--------------|---------|
| Bucket | `github-backups` | Storage for backup files |
| Policy | `pGitHubBackups` | Permissions for bucket access |
| Group | `gGitHubBackups` | Group with policy attached |
| User | `github-backups` | Service account with access key |

### Usage

```bash
# Basic usage (reads from .env or uses defaults)
python tools/setup-bucket.py

# With MinIO admin credentials
python tools/setup-bucket.py \
  --endpoint https://minio.example.com \
  --admin-key admin \
  --admin-secret supersecret

# Custom names
python tools/setup-bucket.py \
  --bucket my-backups \
  --policy pMyBackups \
  --group gMyBackups \
  --user my-backup-user

# Don't update .env file
python tools/setup-bucket.py --no-update-env
```

### Environment Variables

The script reads these environment variables as defaults:

| Variable | Description |
|----------|-------------|
| `S3_ENDPOINT_URL` | MinIO endpoint URL |
| `S3_BUCKET` | Bucket name |
| `S3_REGION` | S3 region |
| `MINIO_ROOT_USER` | Admin access key |
| `MINIO_ROOT_PASSWORD` | Admin secret key |

### Output

On success, the script:
1. Creates all IAM resources
2. Generates a new access key for the service user
3. Prints credentials to console
4. Updates the `.env` file with:
   - `S3_ACCESS_KEY`
   - `S3_SECRET_KEY`
   - `S3_BUCKET`
   - `S3_ENDPOINT_URL`
   - `S3_REGION`

### Re-running

The script is idempotent - you can run it multiple times:
- Existing bucket is kept (not recreated)
- Policy is updated with current permissions
- User/group are preserved
- A **new** access key is generated each time

> **Note**: Each run generates a new access key. Old keys remain valid unless manually removed.

### IAM Policy Permissions

The generated policy includes all permissions needed for backup operations:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "s3:GetBucketLocation",
      "s3:ListBucket",
      "s3:ListBucketMultipartUploads",
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListMultipartUploadParts",
      "s3:AbortMultipartUpload"
    ],
    "Resource": [
      "arn:aws:s3:::github-backups",
      "arn:aws:s3:::github-backups/*"
    ]
  }]
}
```
