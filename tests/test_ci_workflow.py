"""CI workflow shape and deploy-script safety checks."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci-deploy.yml"
DEPLOY = ROOT / "scripts" / "deploy.sh"

DEPLOY_IF = (
    "github.ref == 'refs/heads/V2.0' && "
    "(github.event_name == 'push' || github.event_name == 'workflow_dispatch')"
)


def _load_workflow():
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _triggers(data):
    # PyYAML 1.1 treats the bare key "on" as boolean True.
    if "on" in data:
        return data["on"]
    if True in data:
        return data[True]
    raise AssertionError("workflow is missing its trigger block")


def _norm(value):
    return " ".join(str(value).split())


def test_workflow_parses_and_triggers_on_v2():
    data = _load_workflow()
    triggers = _triggers(data)
    assert "V2.0" in triggers["pull_request"]["branches"]
    assert "V2.0" in triggers["push"]["branches"]
    assert "workflow_dispatch" in triggers
    assert "test" in data["jobs"]
    assert "if" not in data["jobs"]["test"]


def test_deploy_job_is_gated_to_v2_push_or_dispatch():
    deploy = _load_workflow()["jobs"]["deploy"]
    assert deploy["needs"] == "test"
    assert _norm(deploy["if"]) == DEPLOY_IF
    assert "pull_request" not in deploy["if"]
    assert deploy["concurrency"]["group"] == "deploy-production"
    assert deploy["concurrency"]["cancel-in-progress"] is False


def test_deploy_ssh_step_uses_repo_secrets_and_server_script():
    steps = _load_workflow()["jobs"]["deploy"]["steps"]
    ssh_steps = [step for step in steps if step.get("uses") == "appleboy/ssh-action@v1.2.5"]
    assert len(ssh_steps) == 1
    params = ssh_steps[0]["with"]
    assert params["host"] == "${{ secrets.SERVER_HOST }}"
    assert params["username"] == "${{ secrets.SERVER_USER }}"
    assert params["key"] == "${{ secrets.DEPLOY_KEY }}"
    script = params["script"]
    assert "git pull --ff-only origin V2.0" in script
    assert "bash /var/www/yehuda100/bot3/scripts/deploy.sh --baseline" in script
    assert "set -euo pipefail" in script
    body = script.split("<<'EOF'\n", 1)[1].rsplit("\nEOF", 1)[0]
    syntax = subprocess.run(
        ["bash", "-n"],
        input=body,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr


def test_test_job_uses_python_312_and_pytest():
    steps = _load_workflow()["jobs"]["test"]["steps"]
    setup = next(step for step in steps if step.get("uses") == "actions/setup-python@v5")
    assert str(setup["with"]["python-version"]) == "3.12"
    checkout = next(step for step in steps if step.get("uses") == "actions/checkout@v4")
    assert checkout["uses"] == "actions/checkout@v4"
    joined = "\n".join(step.get("run", "") for step in steps)
    assert "pip install -r requirements-dev.txt" in joined
    assert "pytest" in joined
    assert "shellcheck scripts/deploy.sh" in joined


def test_workflow_has_no_hardcoded_host_or_key():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
    assert "BEGIN OPENSSH PRIVATE KEY" not in text
    assert "BEGIN RSA PRIVATE KEY" not in text


def test_deploy_script_safety_contract():
    text = DEPLOY.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in text
    assert 'REPO_DIR="/var/www/yehuda100/bot3"' in text
    assert "git fetch origin" in text
    assert 'git pull --ff-only origin "$BRANCH"' in text
    assert "bot3/bin/pip" in text
    assert "requirements.txt" in text
    assert "screen -S Bot3 -p 0 -X stuff $'\\003'" in text
    assert "screen -dmS Bot3 bash -c 'cd /var/www/yehuda100/bot3 && source bot3/bin/activate && python3 main.py; exec bash'" in text
    assert "[0-9]{6,}:[A-Za-z0-9_-]{30,}" in text
    assert "git reset" not in text
    assert "git clean" not in text
    assert "--hard" not in text
    assert DEPLOY.stat().st_mode & 0o111


def test_deploy_script_syntax():
    subprocess.run(["bash", "-n", str(DEPLOY)], check=True)


def test_shellcheck_deploy_script():
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed")
    subprocess.run([shellcheck, str(DEPLOY)], check=True)


def _git(repo: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", *args], cwd=repo, check=True, env=env)


def _bash(repo: Path, code: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", code],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def test_worktree_guard_ignores_untracked_config_and_venv(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "V2.0")
    (repo / "requirements.txt").write_text("flask==1\n", encoding="utf-8")
    _git(repo, "add", "requirements.txt")
    _git(repo, "commit", "-m", "init")
    (repo / "config.py").write_text("BOT_TOKEN = 'dummy'\n", encoding="utf-8")
    (repo / "bot3" / "bin").mkdir(parents=True)

    clean = _bash(repo, f'source "{DEPLOY}"; assert_deployable_worktree')
    assert clean.returncode == 0, clean.stderr

    (repo / "requirements.txt").write_text("flask==1\ndirty\n", encoding="utf-8")
    dirty = _bash(repo, f'source "{DEPLOY}"; assert_deployable_worktree', check=False)
    assert dirty.returncode != 0
    assert "local changes" in dirty.stderr
    _git(repo, "checkout", "--", "requirements.txt")

    _git(repo, "checkout", "-b", "other")
    wrong_branch = _bash(repo, f'source "{DEPLOY}"; assert_deployable_worktree', check=False)
    assert wrong_branch.returncode != 0
    assert "expected V2.0" in wrong_branch.stderr


def test_requirements_install_retries_when_pip_fails(tmp_path):
    """A failed pip must not be skipped on the next run after the pull already happened."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "V2.0")
    (repo / "requirements.txt").write_text("flask==1\n", encoding="utf-8")
    _git(repo, "add", "requirements.txt")
    _git(repo, "commit", "-m", "init")
    old = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    pip = repo / "bot3" / "bin" / "pip"
    pip.parent.mkdir(parents=True)
    log = repo / "pip.log"
    pip.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
    pip.chmod(0o755)
    stamp = repo / ".requirements-installed-blob"

    def sync(left: str, right: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return _bash(
            repo,
            (
                f'source "{DEPLOY}"; REPO_DIR="{repo}"; STAMP_FILE="{stamp}"; '
                f'sync_requirements "{left}" "{right}"'
            ),
            check=check,
        )

    unchanged = sync(old, old)
    assert unchanged.returncode == 0, unchanged.stderr
    assert not log.exists()
    old_blob = subprocess.check_output(
        ["git", "rev-parse", f"{old}:requirements.txt"], cwd=repo, text=True
    ).strip()
    assert stamp.read_text(encoding="utf-8").strip() == old_blob

    (repo / "requirements.txt").write_text("flask==2\n", encoding="utf-8")
    _git(repo, "add", "requirements.txt")
    _git(repo, "commit", "-m", "bump")
    new = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    new_blob = subprocess.check_output(
        ["git", "rev-parse", f"{new}:requirements.txt"], cwd=repo, text=True
    ).strip()

    failed = sync(old, new, check=False)
    assert failed.returncode != 0
    assert stamp.read_text(encoding="utf-8").strip() == old_blob
    assert not log.exists()

    pip.write_text(f"#!/bin/bash\necho ran >> '{log}'\n", encoding="utf-8")
    pip.chmod(0o755)
    # Same HEAD on both sides: the outer CI pull already fast-forwarded.
    retried = sync(new, new)
    assert retried.returncode == 0, retried.stderr
    assert log.read_text(encoding="utf-8").strip() == "ran"
    assert stamp.read_text(encoding="utf-8").strip() == new_blob

    again = sync(new, new)
    assert again.returncode == 0, again.stderr
    assert log.read_text(encoding="utf-8").strip() == "ran"


def test_redact_stream_hides_telegram_tokens():
    token = "123456789:AAHabcdefghijklmnopqrstuvwxyz012345"
    script = f"""
source "{DEPLOY}"
printf '%s\\n' 'before {token} after' 'keep 12345:short' | redact_stream
"""
    result = subprocess.run(
        ["bash", "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert token not in result.stdout
    assert "<REDACTED>" in result.stdout
    assert "keep 12345:short" in result.stdout
    assert result.stderr == ""
