#!/usr/bin/env python3
"""
GitHub Backup - MinIO Bucket Setup Script

Creates and configures a MinIO bucket with proper IAM setup:
- Bucket with backup policy (including multipart upload permissions)
- IAM Policy (pGitHubBackups) with S3 permissions
- IAM User (github-backups) with policy attached directly
- Service Account for API access (credentials saved to .env)

The user is created with a 64-character password to prevent console login.
A service account is generated for programmatic API access.

Can be re-run safely to validate/update configuration.

Uses pure Python (minio SDK) - no external tools required.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    from minio import Minio
    from minio.minioadmin import MinioAdmin
    from minio.credentials import StaticProvider
    from minio.error import S3Error, InvalidResponseError, ServerError
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from dotenv import load_dotenv
    import urllib3
except ImportError:
    print("Missing dependencies. Install with: pip install -r tools/requirements.txt")
    sys.exit(1)

# Load .env file from project root
script_dir = Path(__file__).parent
project_root = script_dir.parent
env_file = project_root / ".env"
if env_file.exists():
    load_dotenv(env_file)
else:
    # Try .env.example as fallback for defaults
    env_example = project_root / ".env.example"
    if env_example.exists():
        load_dotenv(env_example)

console = Console()

# Default names
DEFAULT_BUCKET = "github-backups"
DEFAULT_POLICY = "pGitHubBackups"
DEFAULT_USER = "github-backups"


def get_bucket_policy(bucket_name: str) -> dict:
    """Generate bucket policy with full backup permissions including multipart upload."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    # Basic operations
                    "s3:GetBucketLocation",
                    "s3:ListBucket",
                    "s3:ListBucketMultipartUploads",
                    # Object operations
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                    # Multipart upload operations
                    "s3:ListMultipartUploadParts",
                    "s3:AbortMultipartUpload",
                ],
                "Resource": [
                    f"arn:aws:s3:::{bucket_name}",
                    f"arn:aws:s3:::{bucket_name}/*",
                ],
            }
        ],
    }


class MinioAdminWrapper:
    """Wrapper around official MinioAdmin for IAM operations."""

    def __init__(self, endpoint: str, access_key: str, secret_key: str, secure: bool = True):
        """Initialize the admin client using official MinioAdmin.

        Args:
            endpoint: MinIO endpoint (host:port, without scheme)
            access_key: Admin access key
            secret_key: Admin secret key
            secure: Use HTTPS
        """
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.secure = secure

        # Disable SSL warnings for self-signed certificates
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        # Create the official MinioAdmin client
        # Note: MinioAdmin handles payload encryption internally
        # All parameters are keyword-only
        self.admin = MinioAdmin(
            endpoint=endpoint,
            credentials=StaticProvider(access_key, secret_key),
            secure=secure,
            cert_check=False,  # Support self-signed certificates
        )

    def policy_add(self, name: str, policy: dict) -> bool:
        """Add or update an IAM policy."""
        try:
            # MinioAdmin.policy_add can take a dict directly via 'policy' param
            self.admin.policy_add(name, policy=policy)
            return True
        except Exception as e:
            console.print(f"[red]Policy creation failed: {e}[/]")
            return False

    def policy_attach(self, policy_name: str, user: str) -> bool:
        """Attach policy to user."""
        try:
            self.admin.policy_set(policy_name, user=user)
            return True
        except Exception as e:
            console.print(f"[red]Policy attach failed: {e}[/]")
            return False

    def user_add(self, access_key: str, secret_key: str) -> bool:
        """Add a user with access key and secret."""
        try:
            self.admin.user_add(access_key, secret_key)
            return True
        except Exception as e:
            console.print(f"[red]User creation failed: {e}[/]")
            return False

    def policy_get(self, name: str) -> dict | None:
        """Get an IAM policy by name."""
        try:
            result = self.admin.policy_info(name)
            if result:
                return json.loads(result)
            return None
        except Exception:
            return None

    def policy_list(self) -> list[str]:
        """List all IAM policies."""
        try:
            result = self.admin.policy_list()
            if result:
                policies = json.loads(result)
                return list(policies.keys()) if isinstance(policies, dict) else []
            return []
        except Exception:
            return []

    def user_exists(self, access_key: str) -> bool:
        """Check if a user exists."""
        try:
            self.admin.user_info(access_key)
            return True
        except Exception:
            return False

    def user_list(self) -> list[str]:
        """List all users."""
        try:
            result = self.admin.user_list()
            if result:
                users = json.loads(result)
                return list(users.keys()) if isinstance(users, dict) else []
            return []
        except Exception:
            return []



def parse_endpoint(endpoint_url: str) -> tuple[str, bool]:
    """Parse endpoint URL into host:port and secure flag."""
    parsed = urlparse(endpoint_url)
    secure = parsed.scheme == "https"
    host = parsed.netloc or parsed.path
    return host, secure


def create_bucket(client: Minio, bucket_name: str, region: str) -> bool:
    """Create S3 bucket if it doesn't exist."""
    console.print(f"[blue]Checking bucket '{bucket_name}'...[/]")
    try:
        if client.bucket_exists(bucket_name):
            console.print(f"[yellow]  Bucket '{bucket_name}' already exists[/]")
            return True
        else:
            console.print(f"[blue]  Creating bucket '{bucket_name}' in region '{region}'...[/]")
            client.make_bucket(bucket_name, location=region)
            console.print(f"[green]  Bucket '{bucket_name}' created[/]")
            return True
    except (S3Error, InvalidResponseError, ServerError) as e:
        console.print(f"[red]  Failed to create bucket: {e}[/]")
        return False
    except Exception as e:
        console.print(f"[red]  Unexpected error creating bucket: {type(e).__name__}: {e}[/]")
        return False


def create_iam_policy(admin: MinioAdminWrapper, policy_name: str, bucket_name: str) -> bool:
    """Create or update IAM policy."""
    console.print(f"[blue]Setting up IAM policy '{policy_name}'...[/]")

    policy_doc = get_bucket_policy(bucket_name)

    if admin.policy_add(policy_name, policy_doc):
        console.print(f"[green]  Policy '{policy_name}' created/updated[/]")
        return True
    return False


def create_service_account_as_user(endpoint: str, user_name: str, user_password: str, secure: bool) -> tuple[str, str] | None:
    """Authenticate as user and create service account.

    MinIO's add_service_account creates SA for the currently authenticated user.
    So we authenticate as the new user and create a service account.

    Returns (access_key, secret_key) or None on failure.
    """
    try:
        # Create admin client authenticated as the new user
        user_admin = MinioAdmin(
            endpoint=endpoint,
            credentials=StaticProvider(user_name, user_password),
            secure=secure,
            cert_check=False,
        )

        # Service account name based on GITHUB_OWNER or fallback to user_name
        github_owner = os.environ.get("GITHUB_OWNER", "").strip()
        sa_name = github_owner if github_owner else user_name

        # Create service account (for currently authenticated user = our new user)
        result = user_admin.add_service_account(
            name=sa_name,
            description=f"Service account for GitHub Backup ({github_owner or user_name})"
        )

        if result:
            # Result can be bytes, string, or dict
            if isinstance(result, bytes):
                result = result.decode("utf-8")
            if isinstance(result, str):
                data = json.loads(result)
            else:
                data = result

            # MinIO returns: {"credentials": {"accessKey": "...", "secretKey": "..."}}
            creds = data.get("credentials", data)
            access_key = creds.get("accessKey") or creds.get("access_key")
            secret_key = creds.get("secretKey") or creds.get("secret_key")

            if access_key and secret_key:
                return access_key, secret_key

        return None
    except Exception as e:
        console.print(f"[red]  Service account creation failed: {e}[/]")
        return None


def create_iam_user_with_policy(admin: MinioAdminWrapper, user_name: str, policy_name: str, bucket_name: str, endpoint: str, secure: bool) -> tuple[bool, str, str, str, str]:
    """Create IAM user with policy and service account.

    1. Creates user with 64-char password (admin credentials)
    2. Attaches policy to user
    3. Authenticates as new user
    4. Creates service account (MinIO generates credentials)

    Returns (success, access_key, secret_key, user_name, user_password).
    User credentials are returned for documentation purposes (stored as comments in .env).
    """
    console.print(f"[blue]Setting up IAM user '{user_name}'...[/]")

    import secrets
    import string

    # Generate 64-character secret (prevents console login)
    user_password = "".join(secrets.choice(string.ascii_letters + string.digits + "!@#$%^&*") for _ in range(64))

    # Check if user already exists
    user_exists = admin.user_exists(user_name)
    if user_exists:
        console.print(f"[yellow]  User '{user_name}' already exists[/]")
    else:
        # Create user
        if not admin.user_add(user_name, user_password):
            return False, "", "", "", ""
        console.print(f"[green]  User '{user_name}' created[/]")

    # Attach policy directly to user
    console.print(f"[blue]  Attaching policy '{policy_name}' to user...[/]")
    if admin.policy_attach(policy_name, user=user_name):
        console.print(f"[green]  Policy '{policy_name}' attached[/]")
    else:
        console.print(f"[yellow]  Warning: Could not attach policy to user[/]")

    # If user already existed, we don't have the password
    if user_exists:
        console.print(f"[yellow]  Cannot create service account (user password unknown)[/]")
        console.print(f"[dim]    Delete user in MinIO Console and re-run, or use existing credentials.[/]")
        return True, user_name, "", "", ""

    # Create service account by authenticating as the new user
    console.print(f"[blue]  Creating service account (authenticating as '{user_name}')...[/]")
    sa_result = create_service_account_as_user(endpoint, user_name, user_password, secure)

    if sa_result:
        access_key, secret_key = sa_result
        console.print(f"[green]  Service account created[/]")
        console.print(f"[dim]    Access Key: {access_key}[/]")
        return True, access_key, secret_key, user_name, user_password
    else:
        # Fallback: use user credentials directly
        console.print(f"[yellow]  Service account creation failed - using user credentials[/]")
        console.print(f"[dim]    In MinIO: user_name = access_key, password = secret_key[/]")
        return True, user_name, user_password, user_name, user_password


def update_env_file(
    env_path: Path,
    access_key: str,
    secret_key: str,
    bucket: str,
    endpoint: str,
    region: str,
    iam_user: str = "",
    iam_password: str = "",
):
    """Update .env file with new credentials.

    Args:
        env_path: Path to .env file
        access_key: S3 access key (service account or user)
        secret_key: S3 secret key
        bucket: Bucket name
        endpoint: S3 endpoint URL
        region: S3 region
        iam_user: IAM user name (for documentation comment)
        iam_password: IAM user password (for documentation comment)
    """
    console.print(f"[blue]Updating {env_path}...[/]")

    if not env_path.exists():
        console.print(f"[yellow]  .env file not found, creating from template...[/]")
        template_path = env_path.parent / ".env.example"
        if template_path.exists():
            content = template_path.read_text(encoding="utf-8")
        else:
            content = ""
    else:
        content = env_path.read_text(encoding="utf-8")

    # Update or add each setting
    updates = {
        "S3_ACCESS_KEY": access_key,
        "S3_SECRET_KEY": secret_key,
        "S3_BUCKET": bucket,
        "S3_ENDPOINT_URL": endpoint,
        "S3_REGION": region,
    }

    for key, value in updates.items():
        pattern = rf"^{key}=.*$"
        replacement = f"{key}={value}"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
        else:
            content += f"\n{replacement}"

    # Add IAM user credentials as comments (for documentation)
    if iam_user and iam_password:
        # Remove existing IAM credential comments if present
        content = re.sub(r"^# IAM User Credentials \(for documentation\).*?(?=\n[^#]|\n\n|\Z)", "", content, flags=re.MULTILINE | re.DOTALL)
        content = re.sub(r"^# MINIO_IAM_USER=.*$\n?", "", content, flags=re.MULTILINE)
        content = re.sub(r"^# MINIO_IAM_PASSWORD=.*$\n?", "", content, flags=re.MULTILINE)

        # Add new IAM credential comments after S3_SECRET_KEY
        iam_comment = (
            f"\n# IAM User Credentials (for documentation - created by setup-bucket.py)\n"
            f"# These are the MinIO IAM user credentials. The service account above is derived from this user.\n"
            f"# MINIO_IAM_USER={iam_user}\n"
            f"# MINIO_IAM_PASSWORD={iam_password}"
        )

        # Try to insert after S3_SECRET_KEY line
        pattern = rf"^(S3_SECRET_KEY=.*)$"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, rf"\1{iam_comment}", content, flags=re.MULTILINE)
        else:
            content += iam_comment

    env_path.write_text(content, encoding="utf-8")
    console.print(f"[green]  .env file updated[/]")
    if iam_user and iam_password:
        console.print(f"[dim]    IAM user credentials stored as comments[/]")


def print_credentials(
    access_key: str,
    secret_key: str,
    bucket: str,
    endpoint: str,
):
    """Print S3 credentials in a table."""
    console.print()

    # S3 credentials table (for .env)
    table = Table(title="S3 Credentials (for .env)", show_header=True)
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("S3_ENDPOINT_URL", endpoint)
    table.add_row("S3_BUCKET", bucket)
    table.add_row("S3_ACCESS_KEY", access_key)
    table.add_row("S3_SECRET_KEY", secret_key[:20] + "..." if len(secret_key) > 40 else secret_key)

    console.print(table)
    console.print()


def prompt_for_value(prompt_text: str, default: str = None, secret: bool = False) -> str:
    """Prompt user for a value with optional default."""
    import getpass

    if default:
        display = f"{prompt_text} [{default}]: "
    else:
        display = f"{prompt_text}: "

    if secret:
        value = getpass.getpass(display)
    else:
        value = input(display)

    return value.strip() if value.strip() else default


def compare_policies(current: dict, expected: dict) -> tuple[bool, list[str]]:
    """Compare two policies and return (match, differences)."""
    differences = []

    if not current:
        return False, ["Policy does not exist"]

    current_actions = set()
    expected_actions = set()
    current_resources = set()
    expected_resources = set()

    # Extract actions and resources from current policy
    for stmt in current.get("Statement", []):
        current_actions.update(stmt.get("Action", []))
        current_resources.update(stmt.get("Resource", []))

    # Extract actions and resources from expected policy
    for stmt in expected.get("Statement", []):
        expected_actions.update(stmt.get("Action", []))
        expected_resources.update(stmt.get("Resource", []))

    # Compare actions
    missing_actions = expected_actions - current_actions
    extra_actions = current_actions - expected_actions

    if missing_actions:
        differences.append(f"Missing actions: {', '.join(sorted(missing_actions))}")
    if extra_actions:
        differences.append(f"Extra actions: {', '.join(sorted(extra_actions))}")

    # Compare resources
    missing_resources = expected_resources - current_resources
    extra_resources = current_resources - expected_resources

    if missing_resources:
        differences.append(f"Missing resources: {', '.join(sorted(missing_resources))}")
    if extra_resources:
        differences.append(f"Extra resources: {', '.join(sorted(extra_resources))}")

    return len(differences) == 0, differences


def show_status(s3_client: Minio, admin_client: MinioAdminWrapper,
                bucket_name: str, policy_name: str, user_name: str) -> dict:
    """Show current status of bucket and IAM resources."""
    console.print(Panel("[bold]Current MinIO Status[/]", title="Status"))
    console.print()

    status = {
        "bucket": False,
        "policy": False,
        "policy_match": False,
        "user": False,
    }

    # Check bucket
    try:
        bucket_exists = s3_client.bucket_exists(bucket_name)
        status["bucket"] = bucket_exists
        if bucket_exists:
            console.print(f"[green]✓[/] Bucket [cyan]{bucket_name}[/] exists")
        else:
            console.print(f"[red]✗[/] Bucket [cyan]{bucket_name}[/] does not exist")
    except (S3Error, InvalidResponseError, ServerError) as e:
        console.print(f"[red]✗[/] Bucket check failed: {e}")
    except Exception as e:
        console.print(f"[red]✗[/] Bucket check failed: {type(e).__name__}: {e}")

    console.print()

    # Check policy
    expected_policy = get_bucket_policy(bucket_name)
    current_policy = admin_client.policy_get(policy_name)
    status["policy"] = current_policy is not None

    if current_policy:
        console.print(f"[green]✓[/] Policy [cyan]{policy_name}[/] exists")

        # Compare policies
        policy_match, differences = compare_policies(current_policy, expected_policy)
        status["policy_match"] = policy_match

        if policy_match:
            console.print(f"  [green]✓[/] Policy permissions match expected configuration")
        else:
            console.print(f"  [yellow]![/] Policy permissions differ from expected:")
            for diff in differences:
                console.print(f"    [yellow]- {diff}[/]")
    else:
        console.print(f"[red]✗[/] Policy [cyan]{policy_name}[/] does not exist")

    console.print()

    # Check user
    user_exists = admin_client.user_exists(user_name)
    status["user"] = user_exists

    if user_exists:
        console.print(f"[green]✓[/] User [cyan]{user_name}[/] exists")
    else:
        console.print(f"[red]✗[/] User [cyan]{user_name}[/] does not exist")

    console.print()

    # Summary table
    table = Table(title="Summary")
    table.add_column("Resource", style="cyan")
    table.add_column("Status")
    table.add_column("Notes")

    table.add_row(
        "Bucket",
        "[green]✓[/]" if status["bucket"] else "[red]✗[/]",
        bucket_name
    )
    table.add_row(
        "Policy",
        "[green]✓[/]" if status["policy"] else "[red]✗[/]",
        f"{policy_name}" + (" [yellow](needs update)[/]" if status["policy"] and not status["policy_match"] else "")
    )
    table.add_row(
        "User",
        "[green]✓[/]" if status["user"] else "[red]✗[/]",
        user_name
    )

    console.print(table)
    console.print()

    return status


def main():
    parser = argparse.ArgumentParser(
        description="Setup MinIO bucket and IAM for GitHub Backup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Actions:
  --status   Show current state of bucket and IAM resources
  --update   Update policy if permissions don't match expected
  --create   Create bucket, policy, and user (full setup)

Examples:
  # Show help and available actions
  python setup-bucket.py

  # Check current status
  python setup-bucket.py --status

  # Update policy permissions
  python setup-bucket.py --update

  # Full setup (create everything)
  python setup-bucket.py --create

  # Setup with custom endpoint
  python setup-bucket.py --create \\
    --endpoint https://minio.example.com \\
    --admin-key admin \\
    --admin-secret secret123

  # Setup with custom names
  python setup-bucket.py --create \\
    --bucket my-backups \\
    --policy pMyBackups \\
    --user my-backup-user

Required: MinIO admin credentials (MINIO_ROOT_USER/MINIO_ROOT_PASSWORD)
These are set when MinIO is deployed and have full admin access.
        """
    )

    parser.add_argument("--endpoint",
                        default=os.environ.get("S3_ENDPOINT_URL"),
                        help="MinIO endpoint URL (env: S3_ENDPOINT_URL)")
    parser.add_argument("--admin-key",
                        default=os.environ.get("MINIO_ROOT_USER"),
                        help="MinIO admin access key (env: MINIO_ROOT_USER)")
    parser.add_argument("--admin-secret",
                        default=os.environ.get("MINIO_ROOT_PASSWORD"),
                        help="MinIO admin secret key (env: MINIO_ROOT_PASSWORD)")
    parser.add_argument("--region",
                        default=os.environ.get("S3_REGION", "us-east-1"),
                        help="S3 region (default: S3_REGION env or us-east-1)")
    parser.add_argument("--bucket",
                        default=os.environ.get("S3_BUCKET", DEFAULT_BUCKET),
                        help=f"Bucket name (default: S3_BUCKET env or {DEFAULT_BUCKET})")
    parser.add_argument("--policy",
                        default=DEFAULT_POLICY,
                        help=f"IAM policy name (default: {DEFAULT_POLICY})")
    parser.add_argument("--user",
                        default=DEFAULT_USER,
                        help=f"IAM user name (default: {DEFAULT_USER})")
    parser.add_argument("--env-file",
                        default=".env",
                        help="Path to .env file to update (default: .env)")
    parser.add_argument("--no-update-env",
                        action="store_true",
                        help="Don't update .env file")
    parser.add_argument("--status",
                        action="store_true",
                        help="Show current status of bucket and IAM resources")
    parser.add_argument("--update",
                        action="store_true",
                        help="Update policy if it doesn't match expected permissions")
    parser.add_argument("--create",
                        action="store_true",
                        help="Create bucket, policy, and user (full setup)")

    args = parser.parse_args()

    # Show help if no action specified
    if not args.status and not args.update and not args.create:
        parser.print_help()
        console.print()
        console.print(Panel(
            "[bold]Available Actions:[/]\n\n"
            "[cyan]--status[/]  Check current state of bucket and IAM resources\n"
            "[cyan]--update[/]  Update policy if permissions don't match\n"
            "[cyan]--create[/]  Create bucket, policy, and user with service account",
            title="Quick Reference"
        ))
        sys.exit(0)

    console.print(Panel(
        "[bold]GitHub Backup - MinIO Setup[/]\n\n"
        "This script creates a bucket with IAM policy and user.\n"
        "A service account is generated for API access.\n"
        "You need MinIO admin credentials (root user) to run this.",
        title="MinIO Bucket Setup"
    ))

    # Prompt for missing required values
    if not args.endpoint:
        args.endpoint = prompt_for_value("MinIO endpoint URL", "http://localhost:9000")

    if not args.admin_key:
        args.admin_key = prompt_for_value("MinIO admin access key (MINIO_ROOT_USER)")
        if not args.admin_key:
            console.print("[red]Error: Admin access key is required[/]")
            sys.exit(1)

    if not args.admin_secret:
        args.admin_secret = prompt_for_value("MinIO admin secret key (MINIO_ROOT_PASSWORD)", secret=True)
        if not args.admin_secret:
            console.print("[red]Error: Admin secret key is required[/]")
            sys.exit(1)

    console.print()
    console.print(f"[blue]Endpoint:[/] {args.endpoint}")
    console.print(f"[blue]Bucket:[/]   {args.bucket}")
    console.print(f"[blue]Region:[/]   {args.region}")
    console.print(f"[blue]Admin:[/]    {args.admin_key}")
    console.print()

    # Parse endpoint
    endpoint_host, secure = parse_endpoint(args.endpoint)

    # Create custom HTTP client that accepts self-signed certificates
    # (common in private MinIO deployments)
    http_client = urllib3.PoolManager(
        cert_reqs="CERT_NONE",
        retries=urllib3.Retry(
            total=3,
            backoff_factor=0.2,
            status_forcelist=[500, 502, 503, 504],
        ),
    )

    # Create MinIO S3 client for bucket operations
    s3_client = Minio(
        endpoint_host,
        access_key=args.admin_key,
        secret_key=args.admin_secret,
        secure=secure,
        region=args.region,
        http_client=http_client,
    )

    # Create MinIO Admin client for IAM operations
    admin_client = MinioAdminWrapper(
        endpoint_host,
        args.admin_key,
        args.admin_secret,
        secure=secure,
    )

    # Handle --status flag
    if args.status:
        status = show_status(s3_client, admin_client, args.bucket, args.policy, args.user)

        # Show hints for missing/outdated resources
        if not status["bucket"] or not status["policy"] or not status["user"]:
            console.print("[dim]Run with --create to create missing resources[/]")
        if status["policy"] and not status["policy_match"]:
            console.print("[dim]Run with --update to fix policy permissions[/]")
        sys.exit(0)

    # Handle --update flag
    if args.update:
        console.print(Panel("[bold]Update Mode[/]", title="Update"))
        console.print()

        # Check current policy
        expected_policy = get_bucket_policy(args.bucket)
        current_policy = admin_client.policy_get(args.policy)

        if not current_policy:
            console.print(f"[yellow]Policy '{args.policy}' does not exist. Creating...[/]")
            if create_iam_policy(admin_client, args.policy, args.bucket):
                console.print(f"[green]✓[/] Policy created successfully")
            else:
                console.print(f"[red]✗[/] Failed to create policy")
                sys.exit(1)
        else:
            policy_match, differences = compare_policies(current_policy, expected_policy)
            if policy_match:
                console.print(f"[green]✓[/] Policy '{args.policy}' already has correct permissions")
            else:
                console.print(f"[yellow]![/] Policy needs update:")
                for diff in differences:
                    console.print(f"  [yellow]- {diff}[/]")
                console.print()
                console.print("[blue]Updating policy...[/]")

                if admin_client.policy_add(args.policy, expected_policy):
                    console.print(f"[green]✓[/] Policy '{args.policy}' updated successfully")
                else:
                    console.print(f"[red]✗[/] Failed to update policy")
                    sys.exit(1)

        console.print()
        console.print(Panel("[green]Update completed[/]", title="Done"))
        sys.exit(0)

    # Handle --create flag (full setup mode)
    if args.create:
        # Step 1: Create bucket
        if not create_bucket(s3_client, args.bucket, args.region):
            sys.exit(1)

        # Step 2: Create IAM policy
        if not create_iam_policy(admin_client, args.policy, args.bucket):
            console.print("[yellow]Warning: IAM policy creation failed. You may need to create it manually.[/]")

        # Step 3: Create IAM user with policy attached
        # In MinIO: user_name = S3_ACCESS_KEY, password = S3_SECRET_KEY
        success, access_key, secret_key, iam_user, iam_password = create_iam_user_with_policy(
            admin_client, args.user, args.policy, args.bucket, endpoint_host, secure
        )

        if success:
            # Print credentials
            print_credentials(access_key, secret_key, args.bucket, args.endpoint)

            # Update .env file
            if not args.no_update_env:
                env_path = Path(args.env_file)
                if not env_path.is_absolute():
                    # Look for .env in project root (parent of tools directory)
                    script_dir = Path(__file__).parent
                    project_root = script_dir.parent
                    env_path = project_root / args.env_file

                update_env_file(
                    env_path, access_key, secret_key, args.bucket, args.endpoint, args.region,
                    iam_user, iam_password
                )

            console.print(Panel(
                "[green bold]Setup completed successfully![/]\n\n"
                "The bucket is ready and credentials have been generated.\n"
                "You can now run the backup application.",
                title="Success"
            ))
        else:
            console.print(Panel(
                "[yellow bold]Setup partially completed[/]\n\n"
                "Bucket created but IAM setup may have failed.\n"
                "MinIO Admin API requires specific permissions.\n\n"
                "Options:\n"
                "1. Use MinIO Console to create policy/user manually\n"
                "2. Ensure admin credentials have full admin access\n"
                "3. Use root credentials (MINIO_ROOT_USER/PASSWORD)",
                title="Warning"
            ))
            sys.exit(1)


if __name__ == "__main__":
    main()
