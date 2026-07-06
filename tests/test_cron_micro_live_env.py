import os
import shutil
import stat
import subprocess
from pathlib import Path


SCRIPT = Path("scripts/cron_micro_live.sh")


def _make_temp_micro_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "venv" / "bin").mkdir(parents=True)
    (repo / "logs").mkdir()

    shutil.copy(SCRIPT, repo / "scripts" / "cron_micro_live.sh")

    python_stub = repo / "venv" / "bin" / "python3"
    python_stub.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'PYTHON_STUB_ARGS=%s\\n' \"$*\"\n"
        "printf 'PYTHON_STUB_MAX_DAYS_TO_EXPIRY=%s\\n' \"${MAX_DAYS_TO_EXPIRY:-}\"\n"
    )
    python_stub.chmod(python_stub.stat().st_mode | stat.S_IXUSR)
    return repo


def _run_micro_cron(
    repo: Path,
    env_file: Path,
    *,
    max_days_to_expiry: str | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["ENV_FILE"] = str(env_file)
    if max_days_to_expiry is None:
        env.pop("MAX_DAYS_TO_EXPIRY", None)
    else:
        env["MAX_DAYS_TO_EXPIRY"] = max_days_to_expiry

    return subprocess.run(
        [str(repo / "scripts" / "cron_micro_live.sh")],
        cwd="/",
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _cron_log(repo: Path) -> str:
    return (repo / "logs" / "cron_micro_live.log").read_text()


def test_micro_cron_preserves_inline_max_days_over_fake_env_default(tmp_path: Path) -> None:
    repo = _make_temp_micro_repo(tmp_path)
    env_file = tmp_path / "fake.env"
    env_file.write_text(
        "MAX_DAYS_TO_EXPIRY=14\n"
        "FAKE_SECRET_VALUE=do-not-print-this\n"
    )

    result = _run_micro_cron(repo, env_file, max_days_to_expiry="3")

    assert result.returncode == 0
    log = _cron_log(repo)
    assert "PYTHON_STUB_ARGS=runner.py --mode micro-live --phase phase1" in log
    assert "PYTHON_STUB_MAX_DAYS_TO_EXPIRY=3" in log
    assert "do-not-print-this" not in result.stdout
    assert "do-not-print-this" not in result.stderr
    assert "do-not-print-this" not in log


def test_micro_cron_uses_fake_env_max_days_when_inline_unset(tmp_path: Path) -> None:
    repo = _make_temp_micro_repo(tmp_path)
    env_file = tmp_path / "fake.env"
    env_file.write_text(
        "MAX_DAYS_TO_EXPIRY=14\n"
        "FAKE_SECRET_VALUE=do-not-print-this\n"
    )

    result = _run_micro_cron(repo, env_file)

    assert result.returncode == 0
    log = _cron_log(repo)
    assert "PYTHON_STUB_MAX_DAYS_TO_EXPIRY=14" in log
    assert "do-not-print-this" not in result.stdout
    assert "do-not-print-this" not in result.stderr
    assert "do-not-print-this" not in log


def test_micro_cron_fails_closed_on_missing_env_file(tmp_path: Path) -> None:
    repo = _make_temp_micro_repo(tmp_path)
    missing_env = tmp_path / "missing.env"

    result = _run_micro_cron(repo, missing_env, max_days_to_expiry="3")

    assert result.returncode != 0
    assert "env file missing or unreadable" in result.stderr
    assert not (repo / "logs" / "cron_micro_live.log").exists()


def test_micro_cron_wrapper_uses_secret_safe_env_loader() -> None:
    text = SCRIPT.read_text()

    assert "set -euo pipefail" in text
    assert 'ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"' in text
    assert "grep -v '^#' .env | xargs" not in text
    assert 'source "$ENV_FILE"' in text
    assert "set +x" in text
    assert 'preserve_runtime_override "MAX_DAYS_TO_EXPIRY"' in text
    assert 'preserve_runtime_override "KALSHI_KEY"' not in text
    assert 'preserve_runtime_override "KALSHI_SECRET_FILE"' not in text
