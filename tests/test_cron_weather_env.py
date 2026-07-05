import os
import shutil
import stat
import subprocess
from pathlib import Path


SCRIPT = Path("scripts/cron_weather_trade.sh")


def _make_temp_weather_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "venv" / "bin").mkdir(parents=True)
    (repo / "logs").mkdir()

    shutil.copy(SCRIPT, repo / "scripts" / "cron_weather_trade.sh")

    python_stub = repo / "venv" / "bin" / "python3"
    python_stub.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'PYTHON_STUB_ARGS=%s\\n' \"$*\"\n"
        "printf 'PYTHON_STUB_WEATHER_DRY_RUN=%s\\n' \"${WEATHER_DRY_RUN:-}\"\n"
    )
    python_stub.chmod(python_stub.stat().st_mode | stat.S_IXUSR)
    return repo


def _run_weather_cron(repo: Path, env_file: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["ENV_FILE"] = str(env_file)
    return subprocess.run(
        [str(repo / "scripts" / "cron_weather_trade.sh")],
        cwd="/",
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_weather_cron_loader_accepts_valid_fake_live_env(tmp_path: Path) -> None:
    repo = _make_temp_weather_repo(tmp_path)
    env_file = tmp_path / "fake.env"
    env_file.write_text(
        "WEATHER_DRY_RUN=false\n"
        "WEATHER_LIVE_ENABLED=true\n"
        "WEATHER_MAX_ORDERS_PER_RUN=1\n"
        "WEATHER_MAX_ORDER_USD=0.25\n"
        "WEATHER_MAX_RUN_EXPOSURE_USD=1.00\n"
        "WEATHER_MAX_OPEN_EXPOSURE_USD=2.00\n"
        "FAKE_SECRET_VALUE=do-not-print-this\n"
    )

    result = _run_weather_cron(repo, env_file)

    assert result.returncode == 0
    assert "PYTHON_STUB_ARGS=scripts/run_weather_strategy.py" in result.stdout
    assert "--dry-run" not in result.stdout
    assert "do-not-print-this" not in result.stdout
    assert "do-not-print-this" not in result.stderr


def test_weather_cron_loader_fails_closed_on_missing_live_limit(tmp_path: Path) -> None:
    repo = _make_temp_weather_repo(tmp_path)
    env_file = tmp_path / "fake.env"
    env_file.write_text(
        "WEATHER_DRY_RUN=false\n"
        "WEATHER_LIVE_ENABLED=true\n"
        "WEATHER_MAX_ORDERS_PER_RUN=1\n"
        "WEATHER_MAX_RUN_EXPOSURE_USD=1.00\n"
        "WEATHER_MAX_OPEN_EXPOSURE_USD=2.00\n"
        "FAKE_SECRET_VALUE=do-not-print-this\n"
    )

    result = _run_weather_cron(repo, env_file)

    assert result.returncode != 0
    assert "WEATHER_MAX_ORDER_USD required for live weather" in result.stderr
    assert "PYTHON_STUB_ARGS" not in result.stdout
    assert "do-not-print-this" not in result.stdout
    assert "do-not-print-this" not in result.stderr


def test_weather_cron_loader_fails_closed_on_missing_env_file(tmp_path: Path) -> None:
    repo = _make_temp_weather_repo(tmp_path)
    missing_env = tmp_path / "missing.env"

    result = _run_weather_cron(repo, missing_env)

    assert result.returncode != 0
    assert "env file missing or unreadable" in result.stderr
    assert "PYTHON_STUB_ARGS" not in result.stdout


def test_weather_cron_defaults_to_dry_run_with_fake_env(tmp_path: Path) -> None:
    repo = _make_temp_weather_repo(tmp_path)
    env_file = tmp_path / "fake.env"
    env_file.write_text("FAKE_SECRET_VALUE=do-not-print-this\n")

    result = _run_weather_cron(repo, env_file)

    assert result.returncode == 0
    assert "PYTHON_STUB_ARGS=scripts/run_weather_strategy.py --dry-run" in result.stdout
    assert "PYTHON_STUB_WEATHER_DRY_RUN=true" in result.stdout
    assert "do-not-print-this" not in result.stdout
    assert "do-not-print-this" not in result.stderr


def test_weather_cron_wrapper_has_no_grep_xargs_env_loader() -> None:
    text = SCRIPT.read_text()

    assert "set -euo pipefail" in text
    assert 'ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"' in text
    assert "grep -v '^#' .env | xargs" not in text
    assert 'source "$ENV_FILE"' in text
    assert "set +x" in text
