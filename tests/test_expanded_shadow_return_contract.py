from __future__ import annotations

import ast
import inspect
import json
import logging
from pathlib import Path
import runpy
import sqlite3
import sys
import textwrap

import pytest

from strategies import kalshi_optimize
from research.candidate_ledger.spool import scan_spool
import utils.kalshi as kalshi_api


STRATEGY_PATH = Path(kalshi_optimize.__file__).resolve()


def _configure_no_markets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kalshi_optimize, "fetch_kalshi_markets", lambda: [])


def test_no_markets_returns_canonical_three_value_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_no_markets(monkeypatch)
    monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "false")
    monkeypatch.delenv("CANDIDATE_LEDGER_SPOOL_PATH", raising=False)

    result = kalshi_optimize.optimize_kalshi_strategy(
        mode="shadow", bankroll=100.0, max_pos_usd=10.0, dry_run=True
    )

    assert isinstance(result, tuple)
    assert result == (0, 0, 0)


def test_direct_cli_no_markets_exits_zero_and_writes_exit_marker(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(kalshi_api, "fetch_kalshi_markets", lambda: [])
    monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "false")
    monkeypatch.delenv("CANDIDATE_LEDGER_SPOOL_PATH", raising=False)
    monkeypatch.setattr(sys, "argv", [str(STRATEGY_PATH), "--mode", "shadow"])

    with caplog.at_level(logging.INFO), pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(STRATEGY_PATH), run_name="__main__")

    assert exc_info.value.code == 0
    assert "No markets fetched" in caplog.text
    assert "Exit code: 0" in caplog.text
    assert "TypeError" not in caplog.text
    assert "[SHADOW_CAPTURE] SHADOW_CAPTURE_ATTEMPTED=false" in caplog.text
    assert "[SHADOW_CAPTURE] SHADOW_CAPTURE_WRITTEN_COUNT=0" in caplog.text


def test_no_markets_capture_enabled_is_empty_spool_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    spool = tmp_path / "candidate-spool"
    spool.mkdir()
    _configure_no_markets(monkeypatch)
    monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "true")
    monkeypatch.setenv("CANDIDATE_LEDGER_SPOOL_PATH", str(spool))

    def sqlite_forbidden(*_args, **_kwargs):
        raise AssertionError("runtime SQLite access is forbidden")

    monkeypatch.setattr(sqlite3, "connect", sqlite_forbidden)
    with caplog.at_level(logging.INFO):
        result = kalshi_optimize.optimize_kalshi_strategy(
            mode="shadow", bankroll=100.0, max_pos_usd=10.0, dry_run=True
        )

    assert result == (0, 0, 0)
    assert scan_spool(spool).batch_count == 0
    assert list(spool.iterdir()) == []
    assert "[SHADOW_CAPTURE] SHADOW_CAPTURE_ENABLED=true" in caplog.text
    assert "[SHADOW_CAPTURE] SHADOW_CAPTURE_ATTEMPTED=false" in caplog.text
    assert "[SHADOW_CAPTURE] SHADOW_CAPTURE_WARNING_COUNT=0" in caplog.text
    assert "[SHADOW_CAPTURE] SHADOW_CAPTURE_DROPPED_COUNT=0" in caplog.text
    assert "[SHADOW_CAPTURE] SHADOW_CAPTURE_STATUS=PASS" in caplog.text
    assert "[SHADOW_CAPTURE] RUNTIME_CAPTURE_MODE=spool" in caplog.text


def test_direct_cli_no_markets_capture_enabled_exits_normally(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    spool = tmp_path / "direct-cli-spool"
    spool.mkdir()
    monkeypatch.setattr(kalshi_api, "fetch_kalshi_markets", lambda: [])
    monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "true")
    monkeypatch.setenv("CANDIDATE_LEDGER_SPOOL_PATH", str(spool))
    monkeypatch.setattr(sys, "argv", [str(STRATEGY_PATH), "--mode", "shadow"])

    def sqlite_forbidden(*_args, **_kwargs):
        raise AssertionError("runtime SQLite access is forbidden")

    monkeypatch.setattr(sqlite3, "connect", sqlite_forbidden)
    with caplog.at_level(logging.INFO), pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(STRATEGY_PATH), run_name="__main__")

    assert exc_info.value.code == 0
    assert "Exit code: 0" in caplog.text
    assert "[SHADOW_CAPTURE] SHADOW_CAPTURE_STATUS=PASS" in caplog.text
    assert "[SHADOW_CAPTURE] SHADOW_CAPTURE_WARNING_COUNT=0" in caplog.text
    assert "[SHADOW_CAPTURE] SHADOW_CAPTURE_DROPPED_COUNT=0" in caplog.text
    assert "[SHADOW_CAPTURE] RUNTIME_CAPTURE_MODE=spool" in caplog.text
    assert scan_spool(spool).batch_count == 0
    assert list(spool.iterdir()) == []


def test_direct_cli_no_markets_writes_attributed_terminal_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    status_dir = tmp_path / "run-status"
    status_dir.mkdir()
    spool = tmp_path / "direct-cli-spool"
    spool.mkdir()
    monkeypatch.setattr(kalshi_api, "fetch_kalshi_markets", lambda: [])
    monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "true")
    monkeypatch.setenv("CANDIDATE_LEDGER_SPOOL_PATH", str(spool))
    monkeypatch.setenv("EXPANDED_SHADOW_RUN_ID", "expanded-shadow-20260721T120000Z")
    monkeypatch.setenv("EXPANDED_SHADOW_EXPECTED_SCHEDULED_AT", "2026-07-21T12:00:00Z")
    monkeypatch.setenv("EXPANDED_SHADOW_RUN_STATUS_DIR", str(status_dir))
    monkeypatch.setattr(sys, "argv", [str(STRATEGY_PATH), "--mode", "shadow"])

    with caplog.at_level(logging.INFO), pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(STRATEGY_PATH), run_name="__main__")

    assert exc_info.value.code == 0
    record = json.loads(
        (status_dir / "expanded-shadow-20260721T120000Z.json").read_text()
    )
    assert record["state"] == "COMPLETED"
    assert record["normal_exit_marker"] is True
    assert record["exit_code"] == 0
    assert record["candidates_processed"] == 0
    assert record["total_markets"] == 0
    assert record["capture_mode"] == "spool"
    assert record["candidate_observed_count"] == 0
    assert record["capture_warning_count"] == 0
    assert record["capture_drop_count"] == 0
    assert record["runtime_sqlite_access"] == "none"
    assert scan_spool(spool).batch_count == 0
    assert "TypeError" not in caplog.text
    assert "Traceback" not in caplog.text


def test_direct_cli_exception_writes_failure_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    status_dir = tmp_path / "run-status"
    status_dir.mkdir()
    monkeypatch.setattr(
        kalshi_api,
        "fetch_kalshi_markets",
        lambda: (_ for _ in ()).throw(RuntimeError("synthetic scanner failure")),
    )
    monkeypatch.setenv("CANDIDATE_LEDGER_SHADOW_ENABLED", "false")
    monkeypatch.setenv("EXPANDED_SHADOW_RUN_ID", "expanded-shadow-20260721T120000Z")
    monkeypatch.setenv("EXPANDED_SHADOW_EXPECTED_SCHEDULED_AT", "2026-07-21T12:00:00Z")
    monkeypatch.setenv("EXPANDED_SHADOW_RUN_STATUS_DIR", str(status_dir))
    monkeypatch.setattr(sys, "argv", [str(STRATEGY_PATH), "--mode", "shadow"])

    with pytest.raises(RuntimeError, match="synthetic scanner failure"):
        runpy.run_path(str(STRATEGY_PATH), run_name="__main__")

    record = json.loads(
        (status_dir / "expanded-shadow-20260721T120000Z.json").read_text()
    )
    assert record["state"] == "FAILED"
    assert record["normal_exit_marker"] is False
    assert record["traceback_present"] is True


def test_all_normal_strategy_returns_are_three_value_tuples() -> None:
    source = textwrap.dedent(inspect.getsource(kalshi_optimize.optimize_kalshi_strategy))
    function = ast.parse(source).body[0]
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    returns = [node for node in ast.walk(function) if isinstance(node, ast.Return)]

    assert len(returns) == 2
    for return_node in returns:
        assert isinstance(return_node.value, ast.Tuple)
        assert len(return_node.value.elts) == 3

    assert inspect.signature(
        kalshi_optimize.optimize_kalshi_strategy
    ).return_annotation == tuple[int, int, int]
