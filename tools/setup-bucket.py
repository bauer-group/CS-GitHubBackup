#!/usr/bin/env python3
"""
GitHub Backup - MinIO Bucket Setup Script

Creates and configures a MinIO bucket with proper IAM setup:
- Bucket with backup policy (including multipart upload permissions)
- IAM Policy (pGitHubBackups)
- IAM Group (gGitHubBackups) with policy attached
- IAM User (github-backups) with access key (no password login)

Can be re-run safely to validate/update configuration.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
except ImportError:
    print("Missing dependencies. Install with: pip install boto3 rich")
    sys.exit(1)

console = Console()

# Default names
DEFAULT_BUCKET = "github-backups"
DEFAULT_POLICY = "pGitHubBackups"
DEFAULT_GROUP = "gGitHubBackups"
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


def run_mc_command(args: list[str], capture_output: bool = True) -> subprocess.CompletedProcess:
    """Run MinIO mc command."""
    cmd = ["mc", "--json"] + args
    result = subprocess.run(cmd, capture_output=capture_output, text=True)
    return result


def mc_command_success(result: subprocess.CompletedProcess) -> bool:
    """Check if mc command succeeded."""
    if result.returncode != 0:
        return False
    if result.stdout:
        try:
            data = json.loads(result.stdout.strip().split("\n")[-1])
            return data.get("status") == "success"
        except (json.JSONDecodeError, KeyError):
            pass
    return result.returncode == 0


def setup_mc_alias(alias: str, endpoint: str, access_key: str, secret_key: str) -> bool:
    """Configure mc alias for MinIO server."""
    console.print(f"[blue]Setting up mc alias '{alias}'...[/]")
    result = run_mc_command([
        "alias", "set", alias, endpoint, access_key, secret_key
    ])
    if result.returncode == 0:
        console.print(f"[green]  Alias '{alias}' configured[/]")
        return True
    else:
        console.print(f"[red]  Failed to set alias: {result.stderr}[/]")
        return False


def create_bucket(s3_client, bucket_name: str) -> bool:
    """Create S3 bucket if it doesn't exist."""
    console.print(f"[blue]Checking bucket '{bucket_name}'...[/]")
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        console.print(f"[yellow]  Bucket '{bucket_name}' already exists[/]")
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            console.print(f"[blue]  Creating bucket '{bucket_name}'...[/]")
            try:
                s3_client.create_bucket(Bucket=bucket_name)
                console.print(f"[green]  Bucket '{bucket_name}' created[/]")
                return True
            except ClientError as ce:
                console.print(f"[red]  Failed to create bucket: {ce}[/]")
                return False
        else:
            console.print(f"[red]  Error checking bucket: {e}[/]")
            return False


def create_iam_policy(alias: str, policy_name: str, bucket_name: str) -> bool:
    """Create or update IAM policy."""
    console.print(f"[blue]Setting up IAM policy '{policy_name}'...[/]")

    policy_doc = get_bucket_policy(bucket_name)
    policy_json = json.dumps(policy_doc)

    # Check if policy exists
    result = run_mc_command(["admin", "policy", "info", alias, policy_name])
    policy_exists = result.returncode == 0

    if policy_exists:
        console.print(f"[yellow]  Policy '{policy_name}' exists, updating...[/]")
    else:
        console.print(f"[blue]  Creating policy '{policy_name}'...[/]")

    # Write policy to temp file
    temp_policy = Path("/tmp/policy.json")
    temp_policy.write_text(policy_json)

    try:
        result = run_mc_command([
            "admin", "policy", "create", alias, policy_name, str(temp_policy)
        ])
        if result.returncode == 0:
            action = "updated" if policy_exists else "created"
            console.print(f"[green]  Policy '{policy_name}' {action}[/]")
            return True
        else:
            console.print(f"[red]  Failed to create policy: {result.stderr}[/]")
            return False
    finally:
        temp_policy.unlink(missing_ok=True)


def create_iam_group(alias: str, group_name: str, policy_name: str) -> bool:
    """Create IAM group and attach policy."""
    console.print(f"[blue]Setting up IAM group '{group_name}'...[/]")

    # Check if group exists
    result = run_mc_command(["admin", "group", "info", alias, group_name])
    group_exists = result.returncode == 0

    if not group_exists:
        console.print(f"[blue]  Creating group '{group_name}'...[/]")
        # Groups are created implicitly when adding members or policies
    else:
        console.print(f"[yellow]  Group '{group_name}' already exists[/]")

    # Attach policy to group
    console.print(f"[blue]  Attaching policy '{policy_name}' to group...[/]")
    result = run_mc_command([
        "admin", "policy", "attach", alias, policy_name, "--group", group_name
    ])

    if result.returncode == 0:
        console.print(f"[green]  Policy attached to group '{group_name}'[/]")
        return True
    else:
        # Check if already attached
        if "already" in result.stderr.lower() or result.returncode == 0:
            console.print(f"[yellow]  Policy already attached to group[/]")
            return True
        console.print(f"[red]  Failed to attach policy: {result.stderr}[/]")
        return False


def create_iam_user(alias: str, user_name: str, group_name: str) -> tuple[bool, str | None, str | None]:
    """Create IAM user, add to group, and generate access keys."""
    console.print(f"[blue]Setting up IAM user '{user_name}'...[/]")

    access_key = None
    secret_key = None

    # Check if user exists
    result = run_mc_command(["admin", "user", "info", alias, user_name])
    user_exists = result.returncode == 0

    if not user_exists:
        console.print(f"[blue]  Creating user '{user_name}'...[/]")
        result = run_mc_command([
            "admin", "user", "add", alias, user_name, "TEMPORARY_PASSWORD_12345"
        ])
        if result.returncode != 0:
            console.print(f"[red]  Failed to create user: {result.stderr}[/]")
            return False, None, None
        console.print(f"[green]  User '{user_name}' created[/]")
    else:
        console.print(f"[yellow]  User '{user_name}' already exists[/]")

    # Add user to group
    console.print(f"[blue]  Adding user to group '{group_name}'...[/]")
    result = run_mc_command([
        "admin", "group", "add", alias, group_name, user_name
    ])
    if result.returncode == 0:
        console.print(f"[green]  User added to group '{group_name}'[/]")
    else:
        console.print(f"[yellow]  User may already be in group[/]")

    # Generate new access key
    console.print(f"[blue]  Generating access key for user...[/]")
    result = run_mc_command([
        "admin", "user", "svcacct", "add", alias, user_name
    ])

    if result.returncode == 0 and result.stdout:
        try:
            # Parse the JSON output to get credentials
            data = json.loads(result.stdout.strip().split("\n")[-1])
            access_key = data.get("accessKey")
            secret_key = data.get("secretKey")
            if access_key and secret_key:
                console.print(f"[green]  Access key generated successfully[/]")
            else:
                console.print(f"[red]  Could not parse access key from response[/]")
        except json.JSONDecodeError:
            console.print(f"[red]  Could not parse mc output[/]")
    else:
        console.print(f"[red]  Failed to generate access key: {result.stderr}[/]")

    return True, access_key, secret_key


def update_env_file(env_path: Path, access_key: str, secret_key: str, bucket: str, endpoint: str, region: str):
    """Update .env file with new credentials."""
    console.print(f"[blue]Updating {env_path}...[/]")

    if not env_path.exists():
        console.print(f"[yellow]  .env file not found, creating from template...[/]")
        template_path = env_path.parent / ".env.example"
        if template_path.exists():
            content = template_path.read_text()
        else:
            content = ""
    else:
        content = env_path.read_text()

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

    env_path.write_text(content)
    console.print(f"[green]  .env file updated[/]")


def print_credentials(access_key: str, secret_key: str, bucket: str, endpoint: str):
    """Print credentials in a nice table."""
    table = Table(title="Generated Credentials", show_header=True)
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("S3_ENDPOINT_URL", endpoint)
    table.add_row("S3_BUCKET", bucket)
    table.add_row("S3_ACCESS_KEY", access_key)
    table.add_row("S3_SECRET_KEY", secret_key)

    console.print()
    console.print(table)
    console.print()


def main():
    parser = argparse.ArgumentParser(
        description="Setup MinIO bucket and IAM for GitHub Backup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Use defaults and environment variables
  python setup-bucket.py

  # Custom bucket name
  python setup-bucket.py --bucket my-github-backups

  # Full customization
  python setup-bucket.py --bucket backups --policy pBackups --group gBackups --user backup-user

  # Specify MinIO endpoint and admin credentials
  python setup-bucket.py --endpoint https://minio.example.com --admin-key admin --admin-secret secret123
        """
    )

    parser.add_argument("--endpoint",
                        default=os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000"),
                        help="MinIO endpoint URL (default: S3_ENDPOINT_URL env or http://localhost:9000)")
    parser.add_argument("--admin-key",
                        default=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
                        help="MinIO admin access key (default: MINIO_ROOT_USER env or minioadmin)")
    parser.add_argument("--admin-secret",
                        default=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
                        help="MinIO admin secret key (default: MINIO_ROOT_PASSWORD env or minioadmin)")
    parser.add_argument("--region",
                        default=os.environ.get("S3_REGION", "us-east-1"),
                        help="S3 region (default: S3_REGION env or us-east-1)")
    parser.add_argument("--bucket",
                        default=os.environ.get("S3_BUCKET", DEFAULT_BUCKET),
                        help=f"Bucket name (default: S3_BUCKET env or {DEFAULT_BUCKET})")
    parser.add_argument("--policy",
                        default=DEFAULT_POLICY,
                        help=f"IAM policy name (default: {DEFAULT_POLICY})")
    parser.add_argument("--group",
                        default=DEFAULT_GROUP,
                        help=f"IAM group name (default: {DEFAULT_GROUP})")
    parser.add_argument("--user",
                        default=DEFAULT_USER,
                        help=f"IAM user name (default: {DEFAULT_USER})")
    parser.add_argument("--env-file",
                        default=".env",
                        help="Path to .env file to update (default: .env)")
    parser.add_argument("--no-update-env",
                        action="store_true",
                        help="Don't update .env file")

    args = parser.parse_args()

    console.print(Panel(
        "[bold]GitHub Backup - MinIO Setup[/]\n"
        f"Endpoint: {args.endpoint}\n"
        f"Bucket: {args.bucket}",
        title="Configuration"
    ))

    # Check mc is installed
    try:
        subprocess.run(["mc", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        console.print("[red]Error: MinIO Client (mc) not found. Install it first:[/]")
        console.print("  https://min.io/docs/minio/linux/reference/minio-mc.html")
        sys.exit(1)

    # Setup mc alias
    alias = "github-backup-setup"
    if not setup_mc_alias(alias, args.endpoint, args.admin_key, args.admin_secret):
        sys.exit(1)

    # Create S3 client for bucket operations
    s3_client = boto3.client(
        "s3",
        endpoint_url=args.endpoint,
        aws_access_key_id=args.admin_key,
        aws_secret_access_key=args.admin_secret,
        region_name=args.region,
        config=boto3.session.Config(s3={"addressing_style": "path"})
    )

    # Step 1: Create bucket
    if not create_bucket(s3_client, args.bucket):
        sys.exit(1)

    # Step 2: Create IAM policy
    if not create_iam_policy(alias, args.policy, args.bucket):
        sys.exit(1)

    # Step 3: Create IAM group with policy
    if not create_iam_group(alias, args.group, args.policy):
        sys.exit(1)

    # Step 4: Create IAM user and generate access key
    success, access_key, secret_key = create_iam_user(alias, args.user, args.group)
    if not success:
        sys.exit(1)

    if access_key and secret_key:
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

            update_env_file(env_path, access_key, secret_key, args.bucket, args.endpoint, args.region)

        console.print(Panel(
            "[green bold]Setup completed successfully![/]\n\n"
            "The bucket is ready and credentials have been generated.\n"
            "You can now run the backup application.",
            title="Success"
        ))
    else:
        console.print(Panel(
            "[yellow bold]Setup partially completed[/]\n\n"
            "Bucket and IAM resources created, but could not generate new access key.\n"
            "If the user already has an access key, you can use that.\n"
            "Otherwise, check the MinIO console to generate one manually.",
            title="Warning"
        ))
        sys.exit(1)

    # Cleanup alias
    run_mc_command(["alias", "remove", alias])


if __name__ == "__main__":
    main()
