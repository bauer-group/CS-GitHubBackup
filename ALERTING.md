# Alerting Configuration

GitHub Backup supports multiple alerting channels to notify you about backup status.

## Configuration

Enable alerting via environment variables:

```env
# Enable alerting
ALERT_ENABLED=true

# Channels to use (comma-separated): email, webhook, teams
ALERT_CHANNELS=webhook,teams

# When to send alerts: errors, warnings, all
ALERT_LEVEL=all
```

## Channels

### Microsoft Teams

```env
TEAMS_WEBHOOK_URL=https://xxx.webhook.office.com/webhookb2/...
```

Compatible with:
- Microsoft Teams Workflows (recommended)
- Legacy Incoming Webhooks (deprecated, retiring 2026)

### Generic Webhook

```env
WEBHOOK_URL=https://your-endpoint.com/webhook
WEBHOOK_SECRET=optional-hmac-secret
```

### Email (SMTP)

```env
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=alerts@example.com
SMTP_PASSWORD=secret
SMTP_FROM=alerts@example.com
SMTP_TO=admin@example.com,team@example.com
SMTP_TLS=true
```

---

## Payload Schemas

### Generic Webhook Payload

```json
{
  "event": "backup_status",
  "service": "github-backup",
  "timestamp": "2024-01-20T02:15:34.123456",
  "level": "success",
  "level_color": "#28a745",
  "title": "Backup Completed",
  "message": "All repositories backed up successfully.",
  "backup_id": "2024-01-20_02-00-00",
  "github_owner": "my-org",
  "stats": {
    "repos_backed_up": 42,
    "repos_skipped": 5,
    "repos_failed": 0,
    "total_repos": 47,
    "issues": 523,
    "pull_requests": 891,
    "releases": 127,
    "wikis": 38,
    "total_size_bytes": 2576980377,
    "total_size_formatted": "2.4 GB",
    "duration_seconds": 754.23,
    "duration_formatted": "12m 34s",
    "deleted_backups": 2
  },
  "errors": [],
  "is_success": true,
  "is_warning": false,
  "is_error": false
}
```

#### Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `event` | string | Always `"backup_status"` |
| `service` | string | Always `"github-backup"` |
| `timestamp` | string | ISO 8601 timestamp |
| `level` | string | `"success"`, `"warning"`, or `"error"` |
| `level_color` | string | Hex color code for UI display |
| `title` | string | Short summary (e.g., "Backup Completed") |
| `message` | string | Detailed status message |
| `backup_id` | string | Unique backup identifier (timestamp-based) |
| `github_owner` | string | GitHub organization or user name |
| `stats.repos_backed_up` | int | Number of repositories backed up |
| `stats.repos_skipped` | int | Unchanged repos (incremental mode) |
| `stats.repos_failed` | int | Repos that failed to backup |
| `stats.total_repos` | int | Total repos processed |
| `stats.issues` | int | Total issues exported |
| `stats.pull_requests` | int | Total PRs exported |
| `stats.releases` | int | Total releases exported |
| `stats.wikis` | int | Number of wikis backed up |
| `stats.total_size_bytes` | int | Total backup size in bytes |
| `stats.total_size_formatted` | string | Human-readable size |
| `stats.duration_seconds` | float | Backup duration in seconds |
| `stats.duration_formatted` | string | Human-readable duration |
| `stats.deleted_backups` | int | Old backups removed (retention) |
| `errors` | array | List of error messages (if any) |
| `is_success` | bool | Convenience flag for filtering |
| `is_warning` | bool | Convenience flag for filtering |
| `is_error` | bool | Convenience flag for filtering |

#### HTTP Headers

```
Content-Type: application/json
User-Agent: GitHubBackup/1.0
```

If `WEBHOOK_SECRET` is configured, HMAC-SHA256 signature headers are added:

```
X-Signature: <hmac-sha256-hex>
X-Signature-256: sha256=<hmac-sha256-hex>
```

#### Signature Verification (Example)

```python
import hmac
import hashlib

def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)
```

---

### Microsoft Teams Adaptive Card

Teams receives an [Adaptive Card](https://adaptivecards.io/) formatted message:

```json
{
  "type": "message",
  "attachments": [
    {
      "contentType": "application/vnd.microsoft.card.adaptive",
      "contentUrl": null,
      "content": {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "msteams": {
          "width": "Full"
        },
        "body": [
          {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "text": "GitHub Backup: Backup Completed",
            "wrap": true,
            "color": "Good"
          },
          {
            "type": "TextBlock",
            "text": "All repositories backed up successfully.",
            "wrap": true,
            "spacing": "Small"
          },
          {
            "type": "FactSet",
            "facts": [
              { "title": "Status", "value": "✓ SUCCESS" },
              { "title": "Backup ID", "value": "2024-01-20_02-00-00" },
              { "title": "Time", "value": "2024-01-20 02:15:34" },
              { "title": "GitHub Owner", "value": "my-org" },
              { "title": "Repos Backed Up", "value": "42" },
              { "title": "Repos Skipped", "value": "5 (unchanged)" },
              { "title": "Issues", "value": "523" },
              { "title": "Pull Requests", "value": "891" },
              { "title": "Releases", "value": "127" },
              { "title": "Wikis", "value": "38" },
              { "title": "Total Size", "value": "2.4 GB" },
              { "title": "Duration", "value": "12m 34s" },
              { "title": "Old Backups Removed", "value": "2" }
            ],
            "spacing": "Medium"
          }
        ]
      }
    }
  ]
}
```

#### Status Colors

| Level | Color | Icon |
|-------|-------|------|
| Success | `Good` (green) | ✓ |
| Warning | `Warning` (yellow) | ⚠ |
| Error | `Attention` (red) | ✗ |

#### Error Display

When errors occur, an additional container is added:

```json
{
  "type": "Container",
  "style": "attention",
  "items": [
    {
      "type": "TextBlock",
      "text": "Errors",
      "weight": "Bolder",
      "color": "Attention"
    },
    {
      "type": "TextBlock",
      "text": "• repo-1: Connection refused\n• repo-2: Authentication failed",
      "wrap": true,
      "fontType": "Monospace",
      "size": "Small"
    }
  ],
  "spacing": "Medium"
}
```

Note: Only the first 5 errors are displayed, with a count of remaining errors.

---

## Alert Levels

| Setting | Sends |
|---------|-------|
| `errors` | Only error alerts |
| `warnings` | Errors and warnings |
| `all` | All alerts including success |
