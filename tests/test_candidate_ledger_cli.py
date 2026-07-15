from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from research.candidate_ledger.store import CandidateLedger


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [
    ROOT / "scripts/candidate_ledger_init.py",
    ROOT / "scripts/candidate_ledger_validate.py",
    ROOT / "scripts/candidate_ledger_summary.py",
    ROOT / "scripts/candidate_replay_evaluate.py",
]


def _run(script, *args, cwd):
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )


def test_help_is_direct_invocation_safe_and_explicitly_offline(tmp_path):
    for script in SCRIPTS:
        result = _run(script, "--help", cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        help_text = result.stdout.lower()
        assert "offline" in help_text
        assert "network" in help_text or "local" in help_text
        assert "--database" in help_text


def test_database_argument_is_required(tmp_path):
    for script in SCRIPTS:
        result = _run(script, cwd=tmp_path)
        assert result.returncode == 2
        assert "--database" in result.stderr


def test_init_validate_summary_and_replay_are_bounded(tmp_path, candidate_factory):
    database = tmp_path / "ledger.sqlite3"
    init = _run(SCRIPTS[0], "--database", str(database), cwd=tmp_path)
    assert init.returncode == 0, init.stderr
    assert json.loads(init.stdout)["status"] == "PASS"

    candidate = candidate_factory()
    candidate_id = candidate["identity"]["candidate_id"]
    with CandidateLedger(database) as ledger:
        ledger.append_event(
            candidate_id=candidate_id,
            event_type="candidate_observed",
            event_timestamp="2026-01-10T12:00:01Z",
            payload=candidate,
        )
        ledger.append_event(
            candidate_id=candidate_id,
            event_type="fill_observed",
            event_timestamp="2026-01-10T12:01:00Z",
            payload={"fill_status": "filled", "filled_quantity": 2, "average_fill_price": 60, "fees": None},
        )
        ledger.append_event(
            candidate_id=candidate_id,
            event_type="settlement_observed",
            event_timestamp="2026-01-12T12:01:00Z",
            payload={"settlement_result": "yes", "resolved_at": "2026-01-12T12:01:00Z"},
        )

    before = database.stat().st_mtime_ns
    validate = _run(SCRIPTS[1], "--database", str(database), cwd=tmp_path)
    summary = _run(SCRIPTS[2], "--database", str(database), cwd=tmp_path)
    replay = _run(SCRIPTS[3], "--database", str(database), cwd=tmp_path)
    assert all(result.returncode == 0 for result in (validate, summary, replay))
    assert database.stat().st_mtime_ns == before
    validate_data = json.loads(validate.stdout)
    summary_data = json.loads(summary.stdout)
    replay_data = json.loads(replay.stdout)
    assert validate_data["valid"] is True and validate_data["read_only"] is True
    assert summary_data["candidate_count"] == 1 and summary_data["raw_candidates_included"] is False
    assert candidate["market"]["ticker"] not in summary.stdout
    assert replay_data["unknown_fee_count"] == 1
    assert replay_data["raw_candidates_included"] is False
