from pathlib import Path


SCRIPT = Path("scripts/run_hourly_shadow.sh")


def _script_text() -> str:
    return SCRIPT.read_text()


def test_hourly_shadow_wrapper_fails_closed_on_env_file() -> None:
    text = _script_text()

    assert "set -euo pipefail" in text
    assert 'ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"' in text
    assert '[ ! -r "$ENV_FILE" ]' in text
    assert 'source "$ENV_FILE"' in text
    assert 'source "$ENV_FILE" || true' not in text


def test_hourly_shadow_wrapper_resolves_repo_from_script_path() -> None:
    text = _script_text()

    assert "BASH_SOURCE[0]" in text
    assert "SCRIPT_DIR=" in text
    assert "REPO_ROOT=" in text
    assert 'cd "$REPO_ROOT"' in text
    assert "cd /opt/slimy/pm_updown_bot_bundle" not in text


def test_hourly_shadow_wrapper_validates_required_names_only() -> None:
    text = _script_text()

    assert "require_env KALSHI_KEY" in text
    assert "require_readable_file_env KALSHI_SECRET_FILE" in text
    assert "missing required env var: $name" in text
    assert "file referenced by $name is missing or unreadable" in text
    assert "KALSHI_KEY=" not in text
    assert "KALSHI_SECRET=" not in text


def test_hourly_shadow_wrapper_preserves_shadow_entrypoint() -> None:
    text = _script_text()

    assert "python3 scripts/hourly_shadow.py >> logs/hourly.log 2>&1" in text
    assert "--mode" not in text
    assert "notify" not in text.lower()
