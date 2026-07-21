from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from research.candidate_ledger.run_observation import (
    ENV_EXPECTED_AT,
    ENV_RUN_ID,
    ENV_STATUS_DIR,
    ExpandedShadowRunObservation,
    read_run_status,
    status_path,
)
from scripts.expanded_shadow_run_status_redacted import main as status_main
from strategies import kalshi_optimize


EXPECTED = "2026-07-21T12:00:00Z"
NOW = datetime(2026, 7, 21, 12, 5, tzinfo=timezone.utc)


def _environment(directory: Path, run_id: str = "expanded-shadow-20260721T120000Z") -> dict[str, str]:
    return {
        ENV_RUN_ID: run_id,
        ENV_EXPECTED_AT: EXPECTED,
        ENV_STATUS_DIR: str(directory),
    }


def _observation(directory: Path, run_id: str = "expanded-shadow-20260721T120000Z") -> ExpandedShadowRunObservation:
    return ExpandedShadowRunObservation.from_environment(
        _environment(directory, run_id),
        now=lambda: datetime(2026, 7, 21, 12, 0, 1, tzinfo=timezone.utc),
    )


def _read(
    directory: Path,
    run_id: str = "expanded-shadow-20260721T120000Z",
    *,
    now: datetime = NOW,
    max_age: int = 3600,
    capture_mode: str | None = None,
) -> dict[str, object]:
    return read_run_status(
        status_dir=directory,
        expected_run_id=run_id,
        expected_scheduled_at=EXPECTED,
        now=now,
        max_status_age_seconds=max_age,
        schedule_tolerance_seconds=900,
        required_capture_mode=capture_mode,
    )


def test_never_ran_is_not_started_without_false_completion(tmp_path: Path) -> None:
    status = _read(tmp_path)
    assert status["EXPANDED_SHADOW_RUN_STATUS"] == "NOT_STARTED"
    assert status["NORMAL_EXIT_MARKER"] == "no"


def test_started_without_completion_is_running(tmp_path: Path) -> None:
    observation = _observation(tmp_path)
    assert observation.enabled

    status = _read(tmp_path)
    assert status["EXPANDED_SHADOW_RUN_STATUS"] == "RUNNING"
    assert status["NORMAL_EXIT_MARKER"] == "no"


def test_zero_candidate_completion_has_truthful_zero_capture_counts(tmp_path: Path) -> None:
    observation = _observation(tmp_path)
    observation.record_capture_summary(
        {
            "SHADOW_CAPTURE_ENABLED": "true",
            "RUNTIME_CAPTURE_MODE": "spool",
            "SHADOW_CAPTURE_WARNING_COUNT": 0,
            "SHADOW_CAPTURE_DROPPED_COUNT": 0,
            "CANDIDATE_OBSERVED_COUNT": 0,
            "GATE_EVALUATED_COUNT": 0,
            "ORDER_INTENT_COUNT": 0,
        }
    )
    assert observation.complete(exit_code=0, candidates_processed=0, total_markets=3)

    status = _read(tmp_path, capture_mode="spool")
    assert status["EXPANDED_SHADOW_RUN_STATUS"] == "PASS"
    assert status["CANDIDATE_OBSERVED_COUNT"] == 0
    assert status["GATE_EVALUATED_COUNT"] == 0
    assert status["CAPTURE_WARNING_COUNT"] == 0
    assert status["CAPTURE_DROP_COUNT"] == 0


def test_nonzero_capture_counts_are_attributed_without_invented_order_events(tmp_path: Path) -> None:
    observation = _observation(tmp_path)
    observation.record_capture_summary(
        {
            "SHADOW_CAPTURE_ENABLED": "true",
            "RUNTIME_CAPTURE_MODE": "spool",
            "SHADOW_CAPTURE_WRITTEN_COUNT": 2,
            "SHADOW_CAPTURE_WARNING_COUNT": 0,
            "SHADOW_CAPTURE_DROPPED_COUNT": 0,
            "CANDIDATE_OBSERVED_COUNT": 1,
            "GATE_EVALUATED_COUNT": 1,
            "ORDER_INTENT_COUNT": 0,
            "ORDER_ATTEMPT_COUNT": 0,
            "ORDER_RESULT_COUNT": 0,
        }
    )
    assert observation.complete(exit_code=0, candidates_processed=1, total_markets=4)

    status = _read(tmp_path, capture_mode="spool")
    assert status["EXPANDED_SHADOW_RUN_STATUS"] == "PASS"
    assert status["CANDIDATE_OBSERVED_COUNT"] == 1
    assert status["GATE_EVALUATED_COUNT"] == 1
    assert status["ORDER_INTENT_COUNT"] == 0
    assert status["ORDER_ATTEMPT_COUNT"] == 0
    assert status["ORDER_RESULT_COUNT"] == 0


def test_exception_is_terminal_failure_without_normal_marker(tmp_path: Path) -> None:
    observation = _observation(tmp_path)
    assert observation.fail(TypeError("synthetic redacted failure"))

    status = _read(tmp_path)
    assert status["EXPANDED_SHADOW_RUN_STATUS"] == "FAIL"
    assert status["NORMAL_EXIT_MARKER"] == "no"
    assert status["TYPE_ERROR_PRESENT"] == "yes"
    assert status["TRACEBACK_PRESENT"] == "yes"


def test_nonzero_terminal_exit_is_failure(tmp_path: Path) -> None:
    observation = _observation(tmp_path)
    assert observation.complete(exit_code=2, candidates_processed=0, total_markets=0)

    status = _read(tmp_path)
    assert status["EXPANDED_SHADOW_RUN_STATUS"] == "FAIL"
    assert status["NORMAL_EXIT_MARKER"] == "no"
    assert status["EXIT_CODE"] == 2


def test_stale_previous_status_is_rejected(tmp_path: Path) -> None:
    observation = _observation(tmp_path)
    assert observation.complete(exit_code=0, candidates_processed=0, total_markets=0)

    status = _read(
        tmp_path,
        now=datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc),
        max_age=1800,
    )
    assert status["EXPANDED_SHADOW_RUN_STATUS"] == "WARN"
    assert status["STALE_STATUS_REJECTED"] == "yes"


def test_marker_for_another_invocation_is_rejected(tmp_path: Path) -> None:
    expected_id = "expanded-shadow-20260721T120000Z"
    observation = _observation(tmp_path, expected_id)
    assert observation.complete(exit_code=0, candidates_processed=0, total_markets=0)
    path = status_path(tmp_path, expected_id)
    record = json.loads(path.read_text())
    record["run_id"] = "expanded-shadow-20260721T100000Z"
    path.write_text(json.dumps(record), encoding="utf-8")

    status = _read(tmp_path, expected_id)
    assert status["EXPANDED_SHADOW_RUN_STATUS"] == "FAIL"
    assert status["STALE_STATUS_REJECTED"] == "yes"


def test_wrong_date_directory_is_not_started_not_stale_success(tmp_path: Path) -> None:
    correct = tmp_path / "20260721"
    wrong = tmp_path / "20260720"
    correct.mkdir()
    wrong.mkdir()
    observation = _observation(wrong)
    assert observation.complete(exit_code=0, candidates_processed=0, total_markets=0)

    assert _read(correct)["EXPANDED_SHADOW_RUN_STATUS"] == "NOT_STARTED"


def test_capture_warning_or_required_mode_mismatch_cannot_pass(tmp_path: Path) -> None:
    observation = _observation(tmp_path)
    observation.record_capture_summary(
        {
            "SHADOW_CAPTURE_ENABLED": "true",
            "RUNTIME_CAPTURE_MODE": "spool",
            "SHADOW_CAPTURE_WARNING_COUNT": 1,
            "SHADOW_CAPTURE_DROPPED_COUNT": 0,
        }
    )
    assert observation.complete(exit_code=0, candidates_processed=0, total_markets=0)
    assert _read(tmp_path, capture_mode="spool")["EXPANDED_SHADOW_RUN_STATUS"] == "WARN"

    other = tmp_path / "other"
    other.mkdir()
    disabled = _observation(other)
    assert disabled.complete(exit_code=0, candidates_processed=0, total_markets=0)
    assert _read(other, capture_mode="spool")["EXPANDED_SHADOW_RUN_STATUS"] == "WARN"


def test_status_is_independent_of_log_rotation_or_truncation(tmp_path: Path) -> None:
    observation = _observation(tmp_path)
    assert observation.complete(exit_code=0, candidates_processed=0, total_markets=0)
    unrelated_log = tmp_path / "scanner.log"
    unrelated_log.write_text("old log", encoding="utf-8")
    unrelated_log.write_text("", encoding="utf-8")
    unrelated_log.rename(tmp_path / "scanner.log.1")

    assert _read(tmp_path)["EXPANDED_SHADOW_RUN_STATUS"] == "PASS"


def test_status_record_ignores_raw_and_secret_looking_capture_values(tmp_path: Path) -> None:
    observation = _observation(tmp_path)
    observation.record_capture_summary(
        {
            "SHADOW_CAPTURE_ENABLED": "true",
            "RUNTIME_CAPTURE_MODE": "spool",
            "RAW_CANDIDATE_TITLE": "SECRET_LOOKING_CANDIDATE_TITLE",
            "PROMPT": "SECRET_LOOKING_PROMPT",
            "API_KEY": "SECRET_LOOKING_CREDENTIAL",
            "WEBHOOK_URL": "SECRET_LOOKING_WEBHOOK_VALUE",
        }
    )
    assert observation.complete(exit_code=0, candidates_processed=0, total_markets=0)
    text = status_path(tmp_path, observation.run_id).read_text()
    assert "SECRET_LOOKING" not in text
    assert "RAW_CANDIDATE_TITLE" not in text
    assert '"raw_candidates_included":false' in text
    assert len(text.encode()) < 16 * 1024


def test_observation_callback_failure_does_not_change_scanner_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenObservation:
        def record_capture_summary(self, _summary):
            raise RuntimeError("synthetic observation-only failure")

    monkeypatch.setattr(kalshi_optimize, "fetch_kalshi_markets", lambda: [])
    monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "false")

    assert kalshi_optimize.optimize_kalshi_strategy(
        mode="shadow",
        bankroll=100.0,
        max_pos_usd=10.0,
        dry_run=True,
        run_observation=BrokenObservation(),
    ) == (0, 0, 0)


def test_redacted_reader_is_single_shot_and_bounded(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observation = _observation(tmp_path)
    assert observation.complete(exit_code=0, candidates_processed=0, total_markets=0)

    result = status_main(
        [
            "--status-dir",
            str(tmp_path),
            "--expected-run-id",
            observation.run_id,
            "--expected-scheduled-at",
            EXPECTED,
            "--now",
            "2026-07-21T12:05:00Z",
        ]
    )
    output = capsys.readouterr().out
    assert result == 0
    assert "EXPANDED_SHADOW_RUN_STATUS=PASS" in output
    assert "RUNTIME_SQLITE_ACCESS=none" in output
    assert "RAW_CANDIDATES_INCLUDED=false" in output
    assert len(output.splitlines()) == 23
