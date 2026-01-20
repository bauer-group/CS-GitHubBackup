# GitHub Backup

Automated backup of GitHub repositories to S3-compatible storage (MinIO, AWS S3, Cloudflare R2).

## Features

- **Full Repository Backup** - Git mirror with complete history as portable bundles
- **Incremental Backup** - Only backs up repositories that changed since last backup
- **Metadata Export** - Issues, Pull Requests, and Releases as JSON
- **Wiki Backup** - Repository wikis as separate bundles
- **S3 Storage** - Compatible with MinIO, AWS S3, Cloudflare R2
- **Smart Retention** - Preserves last backup per repository, even for dormant repos
- **Flexible Scheduling** - Daily, weekly, or interval-based backups
- **CLI Management** - List, delete, download, and restore backups
- **Rich Console UI** - Progress bars, tables, and formatted output

---

## How It Works

### Backup Process

The backup runs in four phases:

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                           BACKUP WORKFLOW                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. DISCOVERY          2. GIT BACKUP         3. METADATA         4. UPLOAD │
│  ─────────────         ──────────────        ────────────        ───────── │
│                                                                             │
│  GitHub API            git clone --mirror    GitHub API          boto3     │
│       │                      │                    │                  │     │
│       ▼                      ▼                    ▼                  ▼     │
│  ┌─────────┐           ┌──────────┐         ┌─────────┐        ┌────────┐ │
│  │  List   │           │  Mirror  │         │ Export  │        │ Upload │ │
│  │  Repos  │ ────────► │  Clone   │ ──────► │ Issues  │ ─────► │ to S3  │ │
│  │         │           │          │         │ PRs     │        │        │ │
│  └─────────┘           └────┬─────┘         │ Release │        └────────┘ │
│                             │               └─────────┘              │     │
│                             ▼                                        │     │
│                        ┌──────────┐                                  │     │
│                        │  Create  │                                  │     │
│                        │  Bundle  │ ─────────────────────────────────┘     │
│                        └──────────┘                                        │
│                                                                             │
│  5. RETENTION: Delete old backups exceeding configured count               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Step-by-step:**

1. **Discovery** - Fetches repository list from GitHub API, filtering by configuration (private, forks, archived)
2. **Change Detection** - Compares each repo's `pushed_at` timestamp with the last backup (incremental mode)
3. **Git Backup** - Creates a mirror clone of changed repositories, then packages as portable Git Bundle
4. **Metadata Export** - Exports Issues, Pull Requests, and Releases as JSON files via GitHub API
5. **Upload** - Uploads all files to S3-compatible storage with multipart support for large files
6. **Smart Retention** - Removes old backups but preserves the last backup for each repository

---

### Incremental Backup

By default, the backup system operates in **incremental mode** (`BACKUP_INCREMENTAL=true`), which offers significant advantages:

**How it works:**

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                        INCREMENTAL BACKUP LOGIC                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  For each repository:                                                       │
│                                                                             │
│    GitHub API                    Local State                                │
│    pushed_at ─────────────────► Compare with ──────► Changed?               │
│    "2024-01-15T10:30:00"         last backup                                │
│                                                                             │
│                                     │                                       │
│                          ┌──────────┴──────────┐                            │
│                          │                     │                            │
│                          ▼                     ▼                            │
│                     [Changed]             [Unchanged]                       │
│                     Backup repo           Skip backup                       │
│                     Update state          Keep existing                     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Benefits:**

| Benefit | Description |
|---------|-------------|
| **Faster Backups** | Only changed repos are processed |
| **Less Bandwidth** | Dormant repos don't consume network |
| **Less Storage** | Fewer redundant backups of unchanged data |
| **API Efficiency** | Reduced GitHub API calls for metadata |

**Smart Retention:**

The retention policy is aware of incremental backups:

- Keeps the configured number of most recent backups (`BACKUP_RETENTION_COUNT`)
- **Never deletes** the last backup of any repository, even if it exceeds retention
- Dormant repositories are guaranteed to have at least one backup preserved

**Example scenario:**

```text
Retention: 7 backups
Repo A: Active, backed up daily → 7 recent backups kept
Repo B: Dormant for 30 days   → Last backup from day 1 preserved
Repo C: Dormant for 60 days   → Last backup from day 1 preserved
```

**Important: Code Changes Only**

Incremental mode detects changes based on the repository's `pushed_at` timestamp from GitHub API. This timestamp only updates when code is pushed to the repository.

| Change Type | Detected | Reason |
|-------------|----------|--------|
| Code push (commits) | Yes | Updates `pushed_at` |
| New branch/tag | Yes | Updates `pushed_at` |
| Force push | Yes | Updates `pushed_at` |
| New issues/comments | **No** | Does not update `pushed_at` |
| New pull requests | **No** | Does not update `pushed_at` |
| New releases | **No** | Does not update `pushed_at` |
| Wiki edits | **No** | Does not update `pushed_at` |

If a repository has new issues, PRs, or releases but no code push since the last backup, it will be **skipped** in incremental mode. The metadata in S3 will remain from the previous backup.

**Disable incremental mode:**

To ensure all metadata is always up-to-date, force a full backup every time:

```env
BACKUP_INCREMENTAL=false
```

---

### State Persistence

The backup system maintains a state file (`state.json`) that tracks:

- Last successful backup timestamp
- Per-repository backup state (last `pushed_at`, backup ID)

**State Synchronization:**

The state is stored locally and synced to S3 for persistence across container restarts:

| Situation                     | Behavior                                       |
|-------------------------------|------------------------------------------------|
| **Local exists, S3 missing**  | Local state is used; synced to S3 on next save |
| **Local missing, S3 exists**  | State is restored from S3                      |
| **Both exist**                | Local state is used (no comparison)            |
| **Both missing**              | Fresh start, new state created                 |

> **Note:** When both local and S3 state exist, the local state takes precedence. This ensures consistent behavior when running with persistent volumes.

---

### What Gets Backed Up

| Component | Description | Format |
|-----------|-------------|--------|
| **Git Repository** | Complete history including all branches, tags, and commits | `.bundle` |
| **Wiki** | Repository wiki (if enabled and has content) | `.wiki.bundle` |
| **Issues** | All issues with comments, labels, assignees, milestones | `issues.json` |
| **Pull Requests** | All PRs with reviews, comments, merge status | `pull-requests.json` |
| **Releases** | All releases with assets metadata, changelogs | `releases.json` |

---

### Git Bundle Format

A **Git Bundle** is Git's native portable format for transferring repositories. It contains:

- Complete commit history
- All branches (including remote tracking branches)
- All tags (lightweight and annotated)
- All objects (blobs, trees, commits)

**Key characteristics:**

| Property | Value |
|----------|-------|
| File extension | `.bundle` |
| Compression | zlib (same as Git pack files) |
| Portability | Works offline, no network required |
| Integrity | SHA-1/SHA-256 verified |
| Size | Comparable to `.git` folder |

**Advantages over other formats:**

- **vs. ZIP/TAR archive**: Bundle preserves Git history; archives only capture a snapshot
- **vs. Bare clone**: Bundle is a single portable file; bare clone is a directory structure
- **vs. GitHub export**: Bundle contains full history; GitHub export is a point-in-time snapshot

---

### Technology Stack

| Component | Library | Purpose |
|-----------|---------|---------|
| **GitHub API** | [PyGithub](https://github.com/PyGithub/PyGithub) | Fetch repository list, issues, PRs, releases |
| **Git Operations** | [GitPython](https://github.com/gitpython-developers/GitPython) | Mirror clone, bundle creation |
| **S3 Storage** | [boto3](https://github.com/boto/boto3) | Upload to S3-compatible storage |
| **Scheduler** | [APScheduler](https://github.com/agronholm/apscheduler) | Cron-based job scheduling |
| **CLI** | [Typer](https://github.com/tiangolo/typer) | Command-line interface |
| **Console UI** | [Rich](https://github.com/Textualize/rich) | Progress bars, tables, formatted output |
| **Configuration** | [Pydantic](https://github.com/pydantic/pydantic) | Type-safe settings from environment |
| **Process Manager** | [Tini](https://github.com/krallin/tini) | Proper signal handling in container |

---

## Quick Start

### 1. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings
```

### 2. Run Backup

```bash
# Scheduled mode (runs according to schedule)
docker compose up -d

# Immediate backup
docker compose run --rm github-backup --now
```

---

## S3 Storage Setup

Choose your storage provider and follow the setup guide.

### MinIO (Self-Hosted)

#### Automated Setup (Recommended)

Use the included setup script to create bucket, policy, group, and user automatically:

```bash
# Install dependencies
pip install -r tools/requirements.txt

# Run setup (uses .env or defaults)
python tools/setup-bucket.py \
  --endpoint https://minio.example.com \
  --admin-key minioadmin \
  --admin-secret minioadmin

# Custom names
python tools/setup-bucket.py \
  --bucket my-backups \
  --policy pMyBackups \
  --group gMyBackups \
  --user my-backup-user
```

The script creates:

| Resource | Default Name | Purpose |
|----------|--------------|---------|
| Bucket | `github-backups` | Storage for backup files |
| Policy | `pGitHubBackups` | Permissions for bucket access |
| Group | `gGitHubBackups` | Group with policy attached |
| User | `github-backups` | Service account with access key |

Credentials are printed to console and written to `.env`.

See [tools/README.md](tools/README.md) for full documentation.

#### Manual Setup

**Step 1: Create a Bucket**

```bash
# Using MinIO Client (mc)
mc alias set myminio https://minio.example.com admin password
mc mb myminio/github-backups
```

Or via MinIO Console:

1. Open MinIO Console (usually `https://minio.example.com:9001`)
2. Navigate to **Buckets** → **Create Bucket**
3. Enter bucket name: `github-backups`
4. Click **Create Bucket**

**Step 2: Create Access Credentials**

Via MinIO Console:

1. Navigate to **Access Keys** → **Create Access Key**
2. Click **Create**
3. Copy **Access Key** and **Secret Key**

Or create a dedicated service account:

1. Navigate to **Identity** → **Users** → **Create User**
2. Username: `github-backup`
3. Assign policy: Use custom policy below

**Step 3: Configure .env**

```env
S3_ENDPOINT_URL=https://minio.example.com
S3_BUCKET=github-backups
S3_ACCESS_KEY=your-access-key
S3_SECRET_KEY=your-secret-key
S3_REGION=us-east-1
```

**Step 4: Custom MinIO Policy**

Create a policy with minimum required permissions (including multipart upload):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketLocation",
        "s3:ListBucket",
        "s3:ListBucketMultipartUploads"
      ],
      "Resource": "arn:aws:s3:::github-backups"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListMultipartUploadParts",
        "s3:AbortMultipartUpload"
      ],
      "Resource": "arn:aws:s3:::github-backups/*"
    }
  ]
}
```

> **Note:** Multipart upload permissions are required for large repository bundles (>100MB).

---

### AWS S3

#### Step 1: Create a Bucket

1. Open [AWS S3 Console](https://s3.console.aws.amazon.com/)
2. Click **Create bucket**
3. Bucket name: `github-backups-yourname` (must be globally unique)
4. Region: Choose your preferred region
5. **Block Public Access**: Keep all options enabled (recommended)
6. **Bucket Versioning**: Enable (recommended for backup recovery)
7. Click **Create bucket**

#### Step 2: Create IAM User

1. Open [IAM Console](https://console.aws.amazon.com/iam/)
2. Navigate to **Users** → **Create user**
3. User name: `github-backup-service`
4. Click **Next**

#### Step 3: Create IAM Policy

1. Navigate to **Policies** → **Create policy**
2. Select **JSON** tab and paste:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "GitHubBackupAccess",
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketLocation",
        "s3:ListBucket"
      ],
      "Resource": "arn:aws:s3:::github-backups-yourname"
    },
    {
      "Sid": "GitHubBackupObjects",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::github-backups-yourname/*"
    }
  ]
}
```

3. Name: `GitHubBackupPolicy`
4. Click **Create policy**

#### Step 4: Attach Policy to User

1. Go back to the user `github-backup-service`
2. **Permissions** → **Add permissions** → **Attach policies directly**
3. Search and select `GitHubBackupPolicy`
4. Click **Add permissions**

#### Step 5: Create Access Keys

1. Select user → **Security credentials** tab
2. **Access keys** → **Create access key**
3. Select **Application running outside AWS**
4. Copy **Access key** and **Secret access key**

#### Step 6: Configure .env

```env
S3_ENDPOINT_URL=https://s3.eu-central-1.amazonaws.com
S3_BUCKET=github-backups-yourname
S3_ACCESS_KEY=AKIA...
S3_SECRET_KEY=your-secret-key
S3_REGION=eu-central-1
```

> **Note**: Replace `eu-central-1` with your bucket's region.

---

### Cloudflare R2

#### Step 1: Create a Bucket

1. Open [Cloudflare Dashboard](https://dash.cloudflare.com/)
2. Navigate to **R2 Object Storage** → **Create bucket**
3. Bucket name: `github-backups`
4. Location: Choose automatic or specific region
5. Click **Create bucket**

#### Step 2: Create API Token

1. Navigate to **R2 Object Storage** → **Manage R2 API Tokens**
2. Click **Create API token**
3. Token name: `github-backup`
4. Permissions: **Object Read & Write**
5. Specify bucket: `github-backups` (recommended)
6. TTL: No expiration (or set as needed)
7. Click **Create API Token**
8. Copy **Access Key ID** and **Secret Access Key**

#### Step 3: Get Account ID

1. Your Account ID is visible in the R2 dashboard URL
2. Or navigate to **Overview** → copy **Account ID**

#### Step 4: Configure .env

```env
S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
S3_BUCKET=github-backups
S3_ACCESS_KEY=your-access-key-id
S3_SECRET_KEY=your-secret-access-key
S3_REGION=auto
```

> **Note**: Replace `<account-id>` with your Cloudflare Account ID.

---

## Authentication Modes

The backup system supports two authentication modes:

| Mode | GITHUB_PAT | Capabilities |
|------|------------|--------------|
| **Authenticated** | Set (`ghp_xxx...`) | Private + public repos, 5000 requests/hour, full metadata |
| **Unauthenticated** | Empty or not set | Public repos ONLY, 60 requests/hour, basic metadata |

### Authenticated Mode (Recommended)

With a GitHub Personal Access Token configured, you get:

- Access to **private repositories** (requires `repo` scope)
- **5000 API requests/hour** rate limit
- Full metadata export (issues, PRs, releases)
- Wiki access for private repositories

```env
GITHUB_OWNER=my-organization
GITHUB_PAT=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Unauthenticated Mode (Public Repos Only)

For backing up public repositories without authentication:

- Access to **public repositories only**
- **60 API requests/hour** rate limit
- Useful for backing up open source projects

```env
GITHUB_OWNER=torvalds
GITHUB_PAT=
# or simply omit GITHUB_PAT
```

**Limitations in Unauthenticated Mode:**

| Feature | Authenticated | Unauthenticated |
|---------|---------------|-----------------|
| Public repos | Yes | Yes |
| Private repos | Yes | No |
| Rate limit | 5000/hour | 60/hour |
| Issues export | Full | Full (public repos) |
| PR export | Full | Full (public repos) |
| Releases export | Full | Full (public repos) |
| Wiki backup | Full | Public wikis only |

> **Important:** The main limitation in unauthenticated mode is the **60 requests/hour rate limit**. For organizations with many repositories or repos with many issues/PRs, the backup may hit rate limits and pause. Use authenticated mode for reliable backups of larger accounts.

---

## GitHub Token Setup

### Option 1: Fine-grained PAT (Recommended)

1. Go to [GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens](https://github.com/settings/tokens?type=beta)
2. Click **Generate new token**
3. Token name: `github-backup`
4. Expiration: Choose appropriate duration
5. Resource owner: Select your organization or personal account
6. Repository access: **All repositories** (or select specific ones)

**Required Repository Permissions (Read-only):**

| Permission | Purpose |
|------------|---------|
| **Contents** | Clone repositories (includes wiki via git) |
| **Issues** | Export issues and comments |
| **Pull requests** | Export pull requests |
| **Metadata** | Repository information (automatically included) |

> **Note:** All permissions should be set to **Read-only** - no write access needed. No organization permissions required.

### Option 2: Classic PAT

1. Go to [GitHub Settings → Developer settings → Personal access tokens → Tokens (classic)](https://github.com/settings/tokens)
2. Click **Generate new token (classic)**
3. Token name: `github-backup`
4. Expiration: Choose appropriate duration

**Required Scopes:**

| Scope | Purpose |
|-------|---------|
| `repo` | Full repository access (required for private repos) |
| `public_repo` | Alternative: public repositories only |
| `read:org` | Read organization membership (required for org backups) |

### Configure .env

```env
GITHUB_OWNER=your-org-or-username
GITHUB_PAT=github_pat_...
```

---

## Scheduler Configuration

### Schedule Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `cron` | Run at fixed time (hour/minute/day) | Daily, weekly, or specific days |
| `interval` | Run every N hours | Continuous protection |

### Examples

**Daily at 2:00 AM (default):**

```env
BACKUP_SCHEDULE_MODE=cron
BACKUP_SCHEDULE_HOUR=2
BACKUP_SCHEDULE_MINUTE=0
BACKUP_SCHEDULE_DAY_OF_WEEK=*
```

**Weekdays only at 3:30 AM:**

```env
BACKUP_SCHEDULE_MODE=cron
BACKUP_SCHEDULE_HOUR=3
BACKUP_SCHEDULE_MINUTE=30
BACKUP_SCHEDULE_DAY_OF_WEEK=0,1,2,3,4
```

**Weekly on Sunday at midnight:**

```env
BACKUP_SCHEDULE_MODE=cron
BACKUP_SCHEDULE_HOUR=0
BACKUP_SCHEDULE_MINUTE=0
BACKUP_SCHEDULE_DAY_OF_WEEK=6
```

**Every 6 hours:**

```env
BACKUP_SCHEDULE_MODE=interval
BACKUP_SCHEDULE_INTERVAL_HOURS=6
```

### Day of Week Reference

| Value | Day |
|-------|-----|
| 0 | Monday |
| 1 | Tuesday |
| 2 | Wednesday |
| 3 | Thursday |
| 4 | Friday |
| 5 | Saturday |
| 6 | Sunday |
| * | All days |

---

## Alerting

The backup system supports professional alerting via multiple channels when backups complete, fail, or encounter issues.

### Alert Channels

| Channel | Description | Use Case |
|---------|-------------|----------|
| **Email (SMTP)** | HTML + plain text emails | Team notifications, audit trail |
| **Webhook** | Generic HTTP POST with JSON | Integration with custom systems |
| **Teams** | Microsoft Teams Adaptive Cards | Team collaboration notifications |

### Alert Levels

| Level | Trigger | Color |
|-------|---------|-------|
| `errors` | Only send alerts on failures | Red |
| `warnings` | Send on failures and partial success | Yellow |
| `all` | Send on all outcomes including success | Green |

### Configuration Overview

```env
# Enable alerting
ALERT_ENABLED=true

# Alert level: errors, warnings, all
ALERT_LEVEL=errors

# Active channels (comma-separated)
ALERT_CHANNELS=email,teams
```

---

### Email (SMTP) Configuration

Sends HTML-formatted emails with backup summaries and statistics.

```env
ALERT_CHANNELS=email

# SMTP Server
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_TLS=true
SMTP_SSL=false

# Authentication (optional for anonymous relay)
SMTP_USER=backup@example.com
SMTP_PASSWORD=your-password

# Sender
SMTP_FROM=no-reply@example.com
SMTP_FROM_NAME=GitHub Backup

# Recipients (comma-separated)
SMTP_TO=admin@example.com,team@example.com
```

**Port Reference:**

| Port | Protocol | Config |
|------|----------|--------|
| 25 | SMTP (no encryption) | `SMTP_TLS=false`, `SMTP_SSL=false` |
| 587 | SMTP + STARTTLS | `SMTP_TLS=true`, `SMTP_SSL=false` |
| 465 | SMTPS (implicit TLS) | `SMTP_TLS=false`, `SMTP_SSL=true` |

---

### Generic Webhook Configuration

Sends JSON payloads to any HTTP endpoint. Supports optional HMAC-SHA256 signature verification.

```env
ALERT_CHANNELS=webhook

# Webhook endpoint
WEBHOOK_URL=https://your-service.example.com/webhook/backup

# Optional: HMAC secret for signature (sent as X-Signature header)
WEBHOOK_SECRET=your-secret-key
```

**Payload Structure:**

```json
{
  "event": "backup_status",
  "service": "github-backup",
  "timestamp": "2024-01-15T02:30:00.000000",
  "level": "success",
  "level_color": "28a745",
  "title": "Backup Completed Successfully",
  "message": "Successfully backed up 25 repositories.",
  "backup_id": "2024-01-15_02-00-00",
  "github_owner": "my-organization",
  "stats": {
    "repos_backed_up": 25,
    "repos_skipped": 15,
    "repos_failed": 0,
    "total_repos": 40,
    "issues": 523,
    "pull_requests": 891,
    "releases": 127,
    "wikis": 12,
    "total_size_bytes": 2576980377,
    "total_size_formatted": "2.4 GB",
    "duration_seconds": 754.3,
    "duration_formatted": "12m 34s",
    "deleted_backups": 2
  },
  "errors": [],
  "is_success": true,
  "is_warning": false,
  "is_error": false
}
```

**Signature Verification (Optional):**

If `WEBHOOK_SECRET` is configured, the payload is signed with HMAC-SHA256:

```
X-Signature: <hex-encoded-hmac>
X-Signature-256: sha256=<hex-encoded-hmac>
```

Verify in your receiver:

```python
import hmac
import hashlib

def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

---

### Microsoft Teams Configuration

Sends rich Adaptive Cards to Microsoft Teams channels via Workflows webhook.

```env
ALERT_CHANNELS=teams

# Teams Webhook URL
TEAMS_WEBHOOK_URL=https://xxx.webhook.office.com/webhookb2/...
```

**Setting up Teams Webhook (Workflows - Recommended):**

1. Open the Teams channel where you want alerts
2. Click `...` menu → **Workflows**
3. Search for **"Post to a channel when a webhook request is received"**
4. Configure the workflow and copy the webhook URL
5. Paste the URL into `TEAMS_WEBHOOK_URL`

**Legacy Incoming Webhook (Deprecated):**

Microsoft is retiring Office 365 Connectors by March 2026. Use Workflows instead.

If you must use legacy webhooks:
1. Channel Settings → Connectors → Incoming Webhook
2. Configure and copy the URL

Both URL formats are supported:
- Workflows: `https://xxx.webhook.office.com/webhookb2/...`
- Legacy: `https://outlook.office.com/webhook/...`

---

### Multiple Channels

Enable multiple channels simultaneously:

```env
ALERT_ENABLED=true
ALERT_LEVEL=errors
ALERT_CHANNELS=email,teams,webhook

# Configure each channel...
SMTP_HOST=...
TEAMS_WEBHOOK_URL=...
WEBHOOK_URL=...
```

### Alert Examples

**Success Alert (level=all):**
```
✓ Backup Completed Successfully

Successfully backed up 25 repositories. 15 unchanged repositories were skipped.

Backup ID: 2024-01-15_02-00-00
Duration: 12m 34s
Total Size: 2.4 GB
```

**Warning Alert (level=warnings):**
```
⚠ Backup Completed with Warnings

Backup completed with 2 warning(s). 23 repositories backed up successfully.

Errors:
• repo-a: Failed to export issues (API rate limit)
• repo-b: Wiki clone failed (repository not found)
```

**Error Alert (level=errors):**
```
✗ Backup Failed

Failed to access or create S3 bucket

Errors:
• S3 Error: Access Denied
```

---

## CLI Commands

The CLI provides management and restore capabilities.

```bash
# List all backups
docker compose run --rm github-backup cli list

# Show backup details
docker compose run --rm github-backup cli show 2024-01-15_02-00-00

# Delete a backup
docker compose run --rm github-backup cli delete 2024-01-15_02-00-00

# Download backup to local directory
docker compose run --rm github-backup cli download 2024-01-15_02-00-00 /data/local

# Restore to local directory
docker compose run --rm github-backup cli restore local 2024-01-15_02-00-00 my-repo ./restored

# Restore to GitHub (same or different repo)
docker compose run --rm github-backup cli restore github 2024-01-15_02-00-00 my-repo
docker compose run --rm github-backup cli restore github 2024-01-15_02-00-00 my-repo --target other-org/new-repo

# Restore to any Git remote
docker compose run --rm github-backup cli restore git 2024-01-15_02-00-00 my-repo https://gitlab.com/user/repo.git
```

---

## Restore & Recovery

### Understanding Git Bundle Restore

A Git Bundle is restored using native Git commands. The bundle contains everything needed to recreate the repository.

### Restore Options

#### Option 1: Restore to Local Directory

Extract a backup to a local working directory:

```bash
# Via CLI
docker compose run --rm github-backup cli restore local 2024-01-15_02-00-00 my-repo ./restored

# Manual: Download bundle and clone from it
git clone my-repo.bundle my-repo
cd my-repo
git remote set-url origin https://github.com/org/my-repo.git
```

#### Option 2: Restore to GitHub

Push the backup directly to GitHub (same or different repository):

```bash
# Restore to original repository
docker compose run --rm github-backup cli restore github 2024-01-15_02-00-00 my-repo

# Restore to a different repository
docker compose run --rm github-backup cli restore github 2024-01-15_02-00-00 my-repo --target other-org/new-repo
```

#### Option 3: Restore to Any Git Remote

Push to GitLab, Bitbucket, or any Git server:

```bash
docker compose run --rm github-backup cli restore git 2024-01-15_02-00-00 my-repo https://gitlab.com/user/repo.git
```

### Manual Restore Process

If you need to restore without the CLI:

```bash
# 1. Download the bundle from S3
aws s3 cp s3://bucket/github-backup/2024-01-15_02-00-00/my-repo/my-repo.bundle .

# 2. Verify bundle integrity
git bundle verify my-repo.bundle

# 3. Clone from bundle
git clone my-repo.bundle my-repo
cd my-repo

# 4. View all branches from backup
git branch -a

# 5. Set new remote and push
git remote set-url origin https://github.com/org/my-repo.git
git push --all origin
git push --tags origin
```

### Restore Metadata

Issues, PRs, and Releases are stored as JSON and can be:

- **Reviewed** for historical reference
- **Imported** via GitHub API (requires custom scripting)
- **Migrated** to issue trackers that support JSON import

```bash
# Download metadata
aws s3 cp s3://bucket/github-backup/2024-01-15_02-00-00/my-repo/metadata/ ./metadata --recursive

# View issues
cat metadata/issues.json | jq '.[] | {number, title, state}'
```

### Disaster Recovery Checklist

1. **Identify the backup** - Use `cli list` to find available backups
2. **Verify integrity** - Use `cli show <backup-id>` to check contents
3. **Download locally first** - Use `cli download` for verification before push
4. **Test restore** - Clone bundle locally to verify completeness
5. **Push to remote** - Use `cli restore` to push to target

---

## Configuration Reference

### Required Variables

| Variable | Description |
|----------|-------------|
| `GITHUB_OWNER` | GitHub organization or username |
| `S3_ENDPOINT_URL` | S3-compatible endpoint |
| `S3_BUCKET` | Target bucket name |
| `S3_ACCESS_KEY` | S3 access key |
| `S3_SECRET_KEY` | S3 secret key |

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_PAT` | (empty) | Personal Access Token (required for private repos) |
| `GITHUB_BACKUP_PRIVATE` | `true` | Include private repositories |
| `GITHUB_BACKUP_FORKS` | `false` | Include forked repositories |
| `GITHUB_BACKUP_ARCHIVED` | `true` | Include archived repositories |
| `GITHUB_BACKUP_ALL_ACCESSIBLE` | `false` | Backup all repos the user has access to (not just owned) |
| `BACKUP_RETENTION_COUNT` | `7` | Number of backups to keep |
| `BACKUP_INCLUDE_METADATA` | `true` | Export issues, PRs, releases |
| `BACKUP_INCLUDE_WIKI` | `true` | Backup wiki repositories |
| `BACKUP_INCREMENTAL` | `true` | Only backup changed repositories |
| `BACKUP_SCHEDULE_ENABLED` | `true` | Enable scheduled backups |
| `BACKUP_SCHEDULE_MODE` | `daily` | Schedule mode (daily/weekly/interval) |
| `BACKUP_SCHEDULE_HOUR` | `2` | Hour to run (0-23) |
| `BACKUP_SCHEDULE_MINUTE` | `0` | Minute to run (0-59) |
| `BACKUP_SCHEDULE_DAY_OF_WEEK` | `*` | Days to run (0-6 or *) |
| `BACKUP_SCHEDULE_INTERVAL_HOURS` | `24` | Hours between backups |
| `S3_REGION` | `us-east-1` | S3 region |
| `S3_PREFIX` | (empty) | Optional folder prefix in bucket |
| `ALERT_ENABLED` | `false` | Enable alerting system |
| `ALERT_LEVEL` | `errors` | Alert level (errors/warnings/all) |
| `ALERT_CHANNELS` | (empty) | Active channels (email,webhook,teams) |
| `TZ` | `Etc/UTC` | Container timezone |
| `LOG_LEVEL` | `INFO` | Log verbosity |

---

## Backup Structure

```
s3://bucket/{S3_PREFIX}/{GITHUB_OWNER}/
├── state.json                        # Sync state (for incremental backups)
├── repo-name/                        # Repository folder
│   ├── 2024-01-15_02-00-00/          # Backup timestamp
│   │   ├── repo-name.bundle          # Git bundle (full history)
│   │   ├── repo-name.wiki.bundle     # Wiki bundle (if exists)
│   │   └── metadata/
│   │       ├── issues.json
│   │       ├── pull-requests.json
│   │       └── releases.json
│   └── 2024-01-14_02-00-00/          # Previous backup
│       └── ...
└── another-repo/
    └── 2024-01-15_02-00-00/
        └── ...
```

This structure allows logical browsing: **owner → repository → backup history**

**S3 Prefix Configuration:**

| `S3_PREFIX`        | Resulting Path                                    |
|--------------------|---------------------------------------------------|
| (empty)            | `s3://bucket/{owner}/{repo}/{backup_id}/...`      |
| `github-backup`    | `s3://bucket/github-backup/{owner}/{repo}/...`    |
| `backups/github`   | `s3://bucket/backups/github/{owner}/{repo}/...`   |

---

## Troubleshooting

### Token Authentication Failed

- Ensure your GitHub PAT has the required permissions
- Check that the token hasn't expired
- Verify `GITHUB_OWNER` matches the token's access scope

### S3 Connection Failed

- Verify `S3_ENDPOINT_URL` is correct and accessible
- Check access key and secret key
- Ensure bucket exists or user has permission to create it
- For MinIO: verify the endpoint includes the correct port

### Permission Denied on S3

- Review IAM/access policy permissions
- Ensure policy is attached to the correct user
- Verify bucket name matches policy ARN

### Wiki Backup Skipped

Wikis are optional. A repo may have wiki enabled but no content, which is normal.

### Rate Limiting

For large organizations, GitHub API rate limits may apply. The tool handles this gracefully by pausing when limits are reached.

---

## Security

- **Never commit `.env`** - Contains sensitive credentials
- **Token Permissions** - Use minimum required permissions
- **S3 Bucket** - Enable versioning and appropriate access policies
- **Container User** - Runs as non-root user (UID 1000)
- **Tini Init** - Proper signal handling and zombie process reaping

---

## Roadmap / Planned Features

| Feature | Description | Status |
|---------|-------------|--------|
| **GitHub Discussions** | Export discussions with comments, categories, labels, and reactions | Planned |

### GitHub Discussions Backup

Currently, the following are backed up: Issues, Pull Requests, Releases, Wiki.

**Planned:** Export GitHub Discussions via GraphQL API.

```env
# Future setting (not yet implemented)
BACKUP_INCLUDE_DISCUSSIONS=true
```

This would include:
- Discussion threads with all comments/replies
- Categories and labels
- Reactions (emoji)
- Answered status

> **Note:** GitHub Discussions require the GraphQL API (REST API has limited support), which will be implemented in a future version.

---

## License

MIT License - See [LICENSE](LICENSE)
