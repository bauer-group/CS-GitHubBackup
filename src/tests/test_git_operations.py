"""
GitHub Backup - Git Operations Tests

Covers how the token reaches git: through a credential helper in the
environment, never through the remote URL (see git_env for the libcurl
CVE-2026-94266 background).

Regression: with `https://TOKEN@github.com/...` URLs, private clones failed
with "could not read Username for 'https://github.com'".
"""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from backup import git_operations
from backup.git_operations import GIT_TOKEN_ENV_VAR, GitBackup, git_env

TOKEN = "ghp_test_token_12345"
HELPER_KEY = "credential.https://github.com.helper"
LFS_ACCESS_KEY = "lfs.https://github.com.access"
HAS_GIT = shutil.which("git") is not None
requires_git = pytest.mark.skipif(not HAS_GIT, reason="git binary not installed")

# Variables through which a surrounding git process (hooks, `git -c ...`
# wrappers, rebase -x) could leak its own config or repository into the tests
LEAKY_GIT_VARS = (
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
)


@pytest.fixture
def isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep the developer's system/global git config out of the test."""
    global_config = tmp_path / "gitconfig"
    global_config.write_text("")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    for var in LEAKY_GIT_VARS:
        monkeypatch.delenv(var, raising=False)
    return global_config


def _credential_fill(env: dict[str, str], host: str) -> subprocess.CompletedProcess:
    """Ask git for credentials exactly like an HTTP 401 would."""
    return subprocess.run(
        ["git", "credential", "fill"],
        input=f"protocol=https\nhost={host}\n\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _config_entries(env: dict[str, str]) -> list[tuple[str, str]]:
    """Decode the GIT_CONFIG_KEY_n/VALUE_n pairs from an environment."""
    count = int(env.get("GIT_CONFIG_COUNT", "0"))
    return [(env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"]) for i in range(count)]


class TestGitEnv:
    """Unit tests for the environment handed to git subprocesses."""

    def test_never_prompts(self) -> None:
        assert git_env()["GIT_TERMINAL_PROMPT"] == "0"
        assert git_env(TOKEN)["GIT_TERMINAL_PROMPT"] == "0"

    def test_anonymous_adds_no_credentials(self, isolated_git: Path) -> None:
        env = git_env(None)
        assert "GIT_CONFIG_COUNT" not in env
        assert GIT_TOKEN_ENV_VAR not in env

    def test_entries_are_scoped_to_github(self, isolated_git: Path) -> None:
        env = git_env(TOKEN)
        entries = _config_entries(env)

        assert entries[0] == (HELPER_KEY, "")  # resets inherited helpers
        assert entries[1][0] == HELPER_KEY
        assert entries[2] == (LFS_ACCESS_KEY, "basic")
        assert len(entries) == 3
        assert env[GIT_TOKEN_ENV_VAR] == TOKEN

    def test_token_not_in_any_config_value(self, isolated_git: Path) -> None:
        values = [value for _, value in _config_entries(git_env(TOKEN))]
        assert values
        assert all(TOKEN not in value for value in values)

    def test_appends_to_existing_env_config(
        self, isolated_git: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.autocrlf")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")

        entries = _config_entries(git_env(TOKEN))

        assert entries[0] == ("core.autocrlf", "false")
        assert entries[1] == (HELPER_KEY, "")
        assert len(entries) == 4


@requires_git
class TestCredentialHelper:
    """Run the generated helper through the real git binary."""

    def test_answers_github_with_token(self, isolated_git: Path) -> None:
        result = _credential_fill(git_env(TOKEN), "github.com")

        assert result.returncode == 0, result.stderr
        assert "username=x-access-token" in result.stdout.splitlines()
        assert f"password={TOKEN}" in result.stdout.splitlines()

    def test_never_hands_token_to_other_hosts(self, isolated_git: Path) -> None:
        result = _credential_fill(git_env(TOKEN), "evil.example.com")

        assert TOKEN not in result.stdout
        assert result.returncode != 0  # no helper, prompts disabled

    def test_overrides_stale_global_helper(self, isolated_git: Path) -> None:
        isolated_git.write_text(
            "[credential]\n"
            "\thelper = \"!f() { echo username=stale; echo password=stale; }; f\"\n"
        )

        result = _credential_fill(git_env(TOKEN), "github.com")

        assert result.returncode == 0, result.stderr
        assert f"password={TOKEN}" in result.stdout.splitlines()
        assert "password=stale" not in result.stdout

    def test_token_absent_from_config_listing_and_trace(self, isolated_git: Path) -> None:
        env = {**git_env(TOKEN), "GIT_TRACE": "1"}

        listing = subprocess.run(
            ["git", "config", "--list"], capture_output=True, text=True, env=env, timeout=30
        )
        fill = _credential_fill(env, "github.com")

        assert HELPER_KEY in listing.stdout
        assert TOKEN not in listing.stdout + listing.stderr
        assert fill.returncode == 0, fill.stderr
        assert TOKEN not in fill.stderr  # trace shows the helper unexpanded


class TestGitBackupWiring:
    """GitBackup must hand the token to every git call that talks to GitHub."""

    @pytest.fixture
    def recorded(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        """Record the env of every git invocation instead of running git."""
        calls: list[dict[str, Any]] = []

        def fake_clone_from(url: str, to_path: str, **kwargs: Any) -> None:
            calls.append({"cmd": ["clone", url], "env": kwargs.get("env") or {}})
            Path(to_path).mkdir(parents=True)

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
            calls.append({"cmd": cmd, "env": kwargs.get("env") or {}})
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(git_operations.Repo, "clone_from", fake_clone_from)
        monkeypatch.setattr(git_operations.subprocess, "run", fake_run)
        return calls

    def test_clone_receives_token(self, tmp_path: Path, recorded: list[dict[str, Any]]) -> None:
        GitBackup(tmp_path, token=TOKEN).mirror_clone("https://github.com/o/r.git", "r")

        (clone,) = recorded
        assert clone["cmd"] == ["clone", "https://github.com/o/r.git"]
        assert clone["env"][GIT_TOKEN_ENV_VAR] == TOKEN
        assert clone["env"]["GIT_TERMINAL_PROMPT"] == "0"

    def test_lfs_fetch_receives_token(self, tmp_path: Path, recorded: list[dict[str, Any]]) -> None:
        GitBackup(tmp_path, token=TOKEN).fetch_lfs_objects(tmp_path)

        (fetch,) = recorded
        assert fetch["cmd"][:3] == ["git", "lfs", "fetch"]
        assert fetch["env"][GIT_TOKEN_ENV_VAR] == TOKEN

    def test_anonymous_backup_sends_no_token(
        self, tmp_path: Path, recorded: list[dict[str, Any]]
    ) -> None:
        GitBackup(tmp_path, token=None).mirror_clone("https://github.com/o/r.git", "r")

        (clone,) = recorded
        assert GIT_TOKEN_ENV_VAR not in clone["env"]


class TestSettingsGitToken:
    """main.py and cli.py take the git token from Settings.git_token."""

    def test_returns_configured_pat(self, test_settings) -> None:
        assert test_settings.git_token == test_settings.github_pat

    def test_strips_whitespace(self, test_settings) -> None:
        settings = test_settings.model_copy(update={"github_pat": f"  {TOKEN}\n"})
        assert settings.git_token == TOKEN

    @pytest.mark.parametrize("pat", ["", "   "])
    def test_none_when_anonymous(self, test_settings, pat: str) -> None:
        assert test_settings.model_copy(update={"github_pat": pat}).git_token is None


@requires_git
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="GitPython kill_after_timeout is unsupported on Windows (prod runs Linux)",
)
class TestGitBackupWithToken:
    """The credential config must not disturb normal clone/bundle work."""

    def test_clone_and_bundle_local_repo(self, isolated_git: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        git = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com"]
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        (source / "README.md").write_text("hello\n")
        subprocess.run(git + ["-C", str(source), "add", "."], check=True)
        subprocess.run(git + ["-C", str(source), "commit", "-q", "-m", "init"], check=True)

        backup = GitBackup(tmp_path / "work", token=TOKEN)
        result = backup.clone_and_bundle(str(source), "source")

        assert not result.is_empty
        assert result.bundle_path is not None and result.bundle_path.exists()
        assert result.bundle_size > 0
