import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "proof_compare.py"


def run_compare(left: Path, right: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(left), str(right)],
        check=False,
        text=True,
        capture_output=True,
    )


def test_different_decorative_headers_same_rows_pass(tmp_path: Path) -> None:
    before = tmp_path / "before.txt"
    after = tmp_path / "after.txt"
    before.write_text(
        "=== CRON HASH BEFORE / SANITIZED SNAPSHOT ===\n"
        "abc123  -\n"
        "0 */2 * * * WEATHER_DRY_RUN=true /path/to/cron_weather_trade.sh\n",
        encoding="utf-8",
    )
    after.write_text(
        "=== CRON HASH AFTER / SANITIZED SNAPSHOT ===\n"
        "abc123  -\n"
        "0 */2 * * * WEATHER_DRY_RUN=true /path/to/cron_weather_trade.sh\n",
        encoding="utf-8",
    )

    result = run_compare(before, after)

    assert result.returncode == 0
    assert "PROOF_COMPARE_RESULT=PASS" in result.stdout
    assert "LEFT_ROWS=2" in result.stdout
    assert "RIGHT_ROWS=2" in result.stdout


def test_actual_content_difference_fails(tmp_path: Path) -> None:
    before = tmp_path / "before.txt"
    after = tmp_path / "after.txt"
    before.write_text("=== BEFORE ===\nabc123  -\n", encoding="utf-8")
    after.write_text("=== AFTER ===\ndef456  -\n", encoding="utf-8")

    result = run_compare(before, after)

    assert result.returncode == 1
    assert "PROOF_COMPARE_RESULT=FAIL" in result.stdout


def test_default_output_does_not_print_secret_content(tmp_path: Path) -> None:
    before = tmp_path / "before.txt"
    after = tmp_path / "after.txt"
    secret_before = "SECRET_TOKEN=synthetic-private-before"
    secret_after = "SECRET_TOKEN=synthetic-private-after"
    before.write_text(f"=== BEFORE ===\n{secret_before}\n", encoding="utf-8")
    after.write_text(f"=== AFTER ===\n{secret_after}\n", encoding="utf-8")

    result = run_compare(before, after)
    combined = result.stdout + result.stderr

    assert result.returncode == 1
    assert "PROOF_COMPARE_RESULT=FAIL" in result.stdout
    assert secret_before not in combined
    assert secret_after not in combined
    assert "SECRET_TOKEN" not in combined


def test_same_no_header_files_pass(tmp_path: Path) -> None:
    before = tmp_path / "before.txt"
    after = tmp_path / "after.txt"
    before.write_text("alpha\nbeta\n", encoding="utf-8")
    after.write_text("alpha\nbeta\n", encoding="utf-8")

    result = run_compare(before, after)

    assert result.returncode == 0
    assert "PROOF_COMPARE_RESULT=PASS" in result.stdout
    assert "LEFT_ROWS=2" in result.stdout
    assert "RIGHT_ROWS=2" in result.stdout
