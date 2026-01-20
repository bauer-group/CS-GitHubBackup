# GitHub Backup - Tools

Helper scripts for setup and administration.

## Prerequisites

**Python dependencies only** - no external tools required:

```bash
pip install -r tools/requirements.txt
```

This installs:

- `minio` - MinIO Python SDK (S3 + Admin API)
- `rich` - Beautiful console output
- `urllib3` - HTTP client
- `python-dotenv` - Load .env file

## setup-bucket.py

Creates and configures a MinIO bucket with proper IAM setup for the backup application.

### What it creates

| Resource | Default Name | Purpose |
|----------|--------------|---------|
| Bucket | `github-backups` | Storage for backup files |
| Policy | `pGitHubBackups` | Permissions for bucket access |
| User | `github-backups` | IAM user with policy attached |

The user is created with a 64-character password to prevent MinIO Console login.
In MinIO, user credentials ARE S3 credentials (user_name = access_key, password = secret_key).

### Usage

```bash
# Show help and available actions
python tools/setup-bucket.py

# Check current status of bucket and IAM resources
python tools/setup-bucket.py --status

# Update policy if permissions don't match
python tools/setup-bucket.py --update

# Full setup - create bucket, policy, user
python tools/setup-bucket.py --create

# Setup with custom endpoint
python tools/setup-bucket.py --create \
  --endpoint https://minio.example.com \
  --admin-key admin \
  --admin-secret supersecret

# Setup with custom names
python tools/setup-bucket.py --create \
  --bucket my-backups \
  --policy pMyBackups \
  --user my-backup-user

# Don't update .env file after setup
python tools/setup-bucket.py --create --no-update-env
```

### Actions

| Action | Command | Description |
|--------|---------|-------------|
| **Help** | `python tools/setup-bucket.py` | Shows help and available actions |
| **Status** | `python tools/setup-bucket.py --status` | Shows what exists and if permissions match |
| **Update** | `python tools/setup-bucket.py --update` | Updates policy if permissions differ |
| **Setup** | `python tools/setup-bucket.py --create` | Creates bucket, policy, user with service account |

### Environment Variables

The script reads these environment variables as defaults:

| Variable | Description |
|----------|-------------|
| `S3_ENDPOINT_URL` | MinIO endpoint URL |
| `S3_BUCKET` | Bucket name |
| `S3_REGION` | S3 region |
| `MINIO_ROOT_USER` | Admin access key (temporary) |
| `MINIO_ROOT_PASSWORD` | Admin secret key (temporary) |

> **Security Notice**: `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` are only needed to run this setup script. **Remove them from your `.env` file after setup!** The script creates a dedicated service user with minimal permissions - you don't need admin credentials for normal operation.

### Output

On success, the script:

1. Creates bucket and IAM policy
2. Creates user with 64-character password (no console login)
3. Attaches policy directly to user
4. Prints S3 credentials to console
5. Updates the `.env` file with:
   - `S3_ACCESS_KEY`
   - `S3_SECRET_KEY`
   - `S3_BUCKET`
   - `S3_ENDPOINT_URL`
   - `S3_REGION`

### Re-running

The script is idempotent - you can run it multiple times:

- Existing bucket is kept (not recreated)
- Policy is updated with current permissions
- Existing user is preserved (credentials not changed)

> **Note**: If user already exists, you won't get new credentials. Delete the user in MinIO Console first if you need to regenerate.

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
