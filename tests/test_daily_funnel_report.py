#!/usr/bin/env python3
"""
Tests for Daily Funnel Report — Reliability Phase 1, Module 3
"""

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scripts.daily_funnel_report import (
    _parse_ts,
    _load_jsonl,
    _git_info,
    build_section_header,
    build_section_cron_health,
    build_section_stage_budget,
    build_section_cascade_cursor,
    build_section_provider_health,
    build_section_trading,
    build_section_anomalies,
    build_discord_summary,
    generate_report,
    post_discord,
)


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


def _write_jsonl(path, entries):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _make_stage_budget_events(ts, cron_run_id, stage_name, elapsed, exhausted, budget_seconds=300):
    return {
        "event_type": "stage_budget",
        "timestamp": ts,
        "stage_name": stage_name,
        "budget_seconds": budget_seconds,
        "elapsed_seconds": elapsed,
        "exhausted": exhausted,
        "items_processed": 5,
        "items_skipped": 0,
        "cron_run_id": cron_run_id,
    }


class TestParseTs:
    def test_iso_with_tz(self):
        ts = "2026-04-14T13:45:58.734476+00:00"
        result = _parse_ts(ts)
        assert result is not None
        assert result.year == 2026
        assert result.month == 4
        assert result.day == 14

    def test_iso_without_tz(self):
        ts = "2026-04-14T13:45:58"
        result = _parse_ts(ts)
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_datetime_format(self):
        ts = "2026-04-14 13:45:58"
        result = _parse_ts(ts)
        assert result is not None
        assert result.hour == 13

    def test_invalid(self):
        assert _parse_ts("not a date") is None
        assert _parse_ts("") is None
        assert _parse_ts(None) is None


class TestLoadJsonl:
    def test_loads_matching_entries(self, tmp_dir, now):
        path = tmp_dir / "test.jsonl"
        within = (now - timedelta(hours=1)).isoformat()
        outside = (now - timedelta(hours=48)).isoformat()
        entries = [
            {"ts": within, "event": "a"},
            {"ts": outside, "event": "b"},
        ]
        _write_jsonl(path, entries)
        window_start = now - timedelta(hours=24)
        result = _load_jsonl(path, window_start)
        assert len(result) == 1
        assert result[0]["event"] == "a"

    def test_timestamp_key(self, tmp_dir, now):
        path = tmp_dir / "test.jsonl"
        ts = (now - timedelta(hours=1)).isoformat()
        entries = [{"timestamp": ts, "event": "x"}]
        _write_jsonl(path, entries)
        result = _load_jsonl(path, now - timedelta(hours=24))
        assert len(result) == 1

    def test_missing_file(self, tmp_dir):
        result = _load_jsonl(tmp_dir / "nonexistent.jsonl", datetime.min.replace(tzinfo=timezone.utc))
        assert result == []

    def test_corrupt_line(self, tmp_dir, now):
        path = tmp_dir / "test.jsonl"
        ts = (now - timedelta(hours=1)).isoformat()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            f.write(json.dumps({"ts": ts, "event": "ok"}) + "\n")
            f.write("not json\n")
            f.write(json.dumps({"ts": ts, "event": "ok2"}) + "\n")
        result = _load_jsonl(path, now - timedelta(hours=24))
        assert len(result) == 2


class TestSectionHeader:
    def test_contains_window_info(self, now):
        ws = now - timedelta(hours=24)
        result = build_section_header(24, ws, now, "/tmp")
        assert "SlimyAI Daily Funnel Report" in result
        assert "last 24h" in result
        assert "Generated:" in result

    def test_git_info_fallback(self, now):
        ws = now - timedelta(hours=24)
        result = build_section_header(24, ws, now, "/nonexistent/path")
        assert "unknown" in result


class TestSectionCronHealth:
    def test_empty_no_file(self, tmp_dir, now):
        result = build_section_cron_health(
            str(tmp_dir / "nope.log"), now - timedelta(hours=24), now)
        assert "No cron log found" in result

    def test_empty_no_runs_in_window(self, tmp_dir, now):
        log = tmp_dir / "cron.log"
        old_ts = (now - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
        log.write_text(f"{old_ts},123 INFO MODE: MICRO-LIVE\n{old_ts},456 INFO Exit code: 0\n")
        result = build_section_cron_health(
            str(log), now - timedelta(hours=24), now)
        assert "No cron runs in window" in result

    def test_one_healthy_run(self, tmp_dir, now):
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        run_id = "abcd1234-5678-9abc-def0-1234567890ab"
        log = tmp_dir / "cron.log"
        lines = [
            f"{ts},100 INFO CRON RUN ID: {run_id}",
            f"{ts},200 INFO MODE: MICRO-LIVE",
            f"{ts},300 INFO Exit code: 0",
        ]
        log.write_text("\n".join(lines) + "\n")
        result = build_section_cron_health(
            str(log), now - timedelta(hours=24), now)
        assert "abcd1234" in result
        assert "0" in result
        assert "1 runs" in result


class TestSectionStageBudget:
    def test_no_data(self, tmp_dir, now):
        result = build_section_stage_budget(
            str(tmp_dir), now - timedelta(hours=24), {"ai_cascade": 300})
        assert "No stage budget events" in result

    def test_with_events(self, tmp_dir, now):
        ts = now.isoformat()
        events = [
            _make_stage_budget_events(ts, "r1", "ai_cascade", 50.0, False, 300),
            _make_stage_budget_events(ts, "r2", "ai_cascade", 250.0, True, 300),
        ]
        _write_jsonl(tmp_dir / "stage_budget.jsonl", events)
        result = build_section_stage_budget(
            str(tmp_dir), now - timedelta(hours=24), {"ai_cascade": 300})
        assert "ai_cascade" in result
        assert "SUSPECT" in result

    def test_tight_flag(self, tmp_dir, now):
        ts = now.isoformat()
        events = [
            _make_stage_budget_events(ts, f"r{i}", "kelly_sizing", 55.0, False, 60)
            for i in range(5)
        ]
        _write_jsonl(tmp_dir / "stage_budget.jsonl", events)
        result = build_section_stage_budget(
            str(tmp_dir), now - timedelta(hours=24), {"kelly_sizing": 60})
        assert "TIGHT" in result


class TestSectionCascadeCursor:
    def test_no_data(self, tmp_dir, now):
        result = build_section_cascade_cursor(
            str(tmp_dir), str(tmp_dir / "cursor.json"), now - timedelta(hours=24))
        assert "Cascade + Rotation State" in result

    def test_with_cursor_file(self, tmp_dir, now):
        ts = now.isoformat()
        cursor_file = tmp_dir / "cursor.json"
        cursor_file.write_text(json.dumps({
            "index": 14, "list_hash": "abc123", "total_rotations": 1
        }))
        _write_jsonl(tmp_dir / "cursor_advance.jsonl", [
            {"ts": ts, "event": "cursor_advance", "markets_processed": 15,
             "total_markets": 425, "rotations_completed": 1},
        ])
        result = build_section_cascade_cursor(
            str(tmp_dir), str(cursor_file), now - timedelta(hours=24))
        assert "index=14" in result
        assert "15.0" in result

    def test_cursor_reset_flagged(self, tmp_dir, now):
        ts = now.isoformat()
        _write_jsonl(tmp_dir / "cursor_reset.jsonl", [
            {"ts": ts, "event": "cursor_reset", "reason": "hash changed"},
        ])
        result = build_section_cascade_cursor(
            str(tmp_dir), str(tmp_dir / "cursor.json"), now - timedelta(hours=24))
        assert "Cursor reset" in result


class TestSectionProviderHealth:
    def test_no_breaker_file(self, tmp_dir, now):
        result = build_section_provider_health(
            str(tmp_dir / "nope.json"), str(tmp_dir), now - timedelta(hours=24))
        assert "No breaker state" in result

    def test_all_healthy(self, tmp_dir, now):
        breaker = tmp_dir / "breakers.json"
        breaker.write_text(json.dumps({
            "grok_420": {"state": "CLOSED", "total_calls": 10, "total_failures": 0},
            "gemini": {"state": "CLOSED", "total_calls": 10, "total_failures": 1},
        }))
        result = build_section_provider_health(
            str(breaker), str(tmp_dir), now - timedelta(hours=24))
        assert "grok_420" in result
        assert "All providers healthy" in result

    def test_open_provider_flagged(self, tmp_dir, now):
        breaker = tmp_dir / "breakers.json"
        breaker.write_text(json.dumps({
            "grok_420": {"state": "OPEN", "total_calls": 25, "total_failures": 15},
        }))
        result = build_section_provider_health(
            str(breaker), str(tmp_dir), now - timedelta(hours=24))
        assert "OPEN" in result
        assert "Non-CLOSED providers" in result


class TestSectionTrading:
    def _make_db(self, path, trades=None, equity=None):
        conn = sqlite3.connect(str(path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phase TEXT NOT NULL,
                ticker TEXT NOT NULL,
                action TEXT NOT NULL,
                price REAL NOT NULL,
                size_usd REAL NOT NULL,
                pnl_usd REAL DEFAULT 0,
                pnl_pct REAL DEFAULT 0,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cash REAL NOT NULL,
                position_value REAL NOT NULL,
                total_value REAL NOT NULL,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        if trades:
            for t in trades:
                conn.execute(
                    "INSERT INTO trades (phase, ticker, action, price, size_usd, pnl_usd, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", t)
        if equity:
            for e in equity:
                conn.execute(
                    "INSERT INTO equity_snapshots (cash, position_value, total_value, timestamp) "
                    "VALUES (?, ?, ?, ?)", e)
        conn.commit()
        conn.close()

    def test_no_db(self, tmp_dir, now):
        result = build_section_trading(
            str(tmp_dir / "nope.db"), now - timedelta(hours=24), now)
        assert "No pnl.db found" in result

    def test_empty_db(self, tmp_dir, now):
        db = tmp_dir / "pnl.db"
        self._make_db(db)
        result = build_section_trading(str(db), now - timedelta(hours=24), now)
        assert "Orders placed:** 0" in result

    def test_with_trades(self, tmp_dir, now):
        db = tmp_dir / "pnl.db"
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        ts_prev = (now - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S")
        trades = [
            ("phase1", "KXTICKER1", "BUY", 0.05, 5.00, 0.0, ts),
            ("phase1", "KXTICKER1", "EXIT", 0.08, 5.00, 0.15, ts),
            ("phase1", "KXTICKER2", "BUY", 0.10, 3.00, 0.0, ts_prev),
        ]
        equity = [
            (100.0, 5.0, 105.0, (now - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")),
            (105.15, 0.0, 105.15, ts),
        ]
        self._make_db(db, trades, equity)
        result = build_section_trading(str(db), now - timedelta(hours=24), now)
        assert "Orders placed:** 1" in result
        assert "Realized P&L" in result

    def test_top_orders(self, tmp_dir, now):
        db = tmp_dir / "pnl.db"
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        trades = [
            ("phase1", "KXBIG1", "BUY", 0.05, 10.00, 0.0, ts),
            ("phase1", "KXBIG2", "BUY", 0.10, 8.00, 0.0, ts),
            ("phase1", "KXSMALL", "BUY", 0.02, 1.00, 0.0, ts),
        ]
        self._make_db(db, trades)
        result = build_section_trading(str(db), now - timedelta(hours=24), now)
        assert "KXBIG1" in result


class TestSectionAnomalies:
    def test_no_anomalies(self, tmp_dir, now):
        result = build_section_anomalies(
            str(tmp_dir), str(tmp_dir / "cron.log"), str(tmp_dir / "pnl.db"),
            now - timedelta(hours=24), now, {"ai_cascade": 300})
        assert "No anomalies detected" in result

    def test_canary_failure(self, tmp_dir, now):
        ts = now.isoformat()
        _write_jsonl(tmp_dir / "canary_failure.jsonl", [
            {"ts": ts, "event_type": "canary_failure",
             "failed_check_name": "provider_health",
             "error_message": "all providers timed out"},
        ])
        result = build_section_anomalies(
            str(tmp_dir), str(tmp_dir / "cron.log"), str(tmp_dir / "pnl.db"),
            now - timedelta(hours=24), now, {"ai_cascade": 300})
        assert "CANARY FAILURE" in result

    def test_breaker_open(self, tmp_dir, now):
        import config as cfg
        breaker = cfg.CIRCUIT_BREAKER_PATH
        breaker_backup = None
        if breaker.exists():
            breaker_backup = breaker.read_text()
        try:
            breaker.parent.mkdir(parents=True, exist_ok=True)
            breaker.write_text(json.dumps({
                "grok_420": {"state": "OPEN", "total_calls": 25, "total_failures": 15, "total_opens": 1},
                "gemini": {"state": "CLOSED", "total_calls": 10, "total_failures": 0, "total_opens": 0},
            }))
            result = build_section_anomalies(
                str(tmp_dir), str(tmp_dir / "cron.log"), str(tmp_dir / "pnl.db"),
                now - timedelta(hours=24), now, {"ai_cascade": 300})
            assert "BREAKER OPEN" in result
        finally:
            if breaker_backup:
                breaker.write_text(breaker_backup)
            elif breaker.exists():
                breaker.unlink()

    def test_budget_exhaustion(self, tmp_dir, now):
        ts = now.isoformat()
        _write_jsonl(tmp_dir / "cascade_budget_exhausted.jsonl", [
            {"ts": ts, "event": "cascade_budget_exhausted",
             "markets_with_priors": 9, "markets_total": 20},
        ])
        result = build_section_anomalies(
            str(tmp_dir), str(tmp_dir / "cron.log"), str(tmp_dir / "pnl.db"),
            now - timedelta(hours=24), now, {"ai_cascade": 300})
        assert "BUDGET EXHAUSTION" in result

    def test_traceback_in_cron_log(self, tmp_dir, now):
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        log = tmp_dir / "cron.log"
        log.write_text(f"{ts},123 ERROR Traceback (most recent call last):\n")
        result = build_section_anomalies(
            str(tmp_dir), str(log), str(tmp_dir / "pnl.db"),
            now - timedelta(hours=24), now, {"ai_cascade": 300})
        assert "TRACEBACK" in result

    def test_order_failed(self, tmp_dir, now):
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        log = tmp_dir / "cron.log"
        log.write_text(f"{ts},123 ERROR [MICRO-LIVE] ORDER FAILED: name 'padding' is not defined\n")
        result = build_section_anomalies(
            str(tmp_dir), str(log), str(tmp_dir / "pnl.db"),
            now - timedelta(hours=24), now, {"ai_cascade": 300})
        assert "ORDER FAILED" in result

    def test_unexplained_cash_delta(self, tmp_dir, now):
        db = tmp_dir / "pnl.db"
        conn = sqlite3.connect(str(db))
        conn.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phase TEXT NOT NULL, ticker TEXT NOT NULL, action TEXT NOT NULL,
            price REAL NOT NULL, size_usd REAL NOT NULL, pnl_usd REAL DEFAULT 0,
            pnl_pct REAL DEFAULT 0, timestamp TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cash REAL NOT NULL, position_value REAL NOT NULL,
            total_value REAL NOT NULL,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')))""")
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO trades (phase, ticker, action, price, size_usd, pnl_usd, timestamp) "
                     "VALUES ('p1', 'TKR', 'BUY', 0.05, 5.0, 0.0, ?)", (ts,))
        conn.execute("INSERT INTO equity_snapshots (cash, position_value, total_value, timestamp) "
                     "VALUES (100.0, 5.0, 105.0, ?)", (ts,))
        conn.execute("INSERT INTO equity_snapshots (cash, position_value, total_value, timestamp) "
                     "VALUES (150.0, 0.0, 150.0, ?)", (ts,))
        conn.commit()
        conn.close()
        result = build_section_anomalies(
            str(tmp_dir), str(tmp_dir / "cron.log"), str(db),
            now - timedelta(hours=24), now, {"ai_cascade": 300})
        assert "UNEXPLAINED CASH" in result


class TestDiscordSummary:
    def test_produces_valid_output(self):
        report = (
            "# SlimyAI Daily Funnel Report — 2026-04-14\n\n"
            "## Cron Health Summary\n\n"
            "**Totals:** 4 runs, 4 clean exits, 0 orders placed, 0 orders filled\n\n"
            "## Anomalies and Flags\n\n"
            "No anomalies detected.\n"
        )
        result = build_discord_summary(report, "/tmp/report.md")
        assert "SlimyAI" in result
        assert "Runs:" in result
        assert "Anomalies: 0" in result

    def test_with_anomalies(self):
        report = (
            "# SlimyAI Daily Funnel Report — 2026-04-14\n\n"
            "## Anomalies and Flags\n\n"
            "- **CANARY FAILURE**: provider_health: timeout\n"
            "- **BREAKER OPEN**: grok_420\n"
        )
        result = build_discord_summary(report, "/tmp/report.md")
        assert "Anomalies: 2" in result


class TestPostDiscord:
    def test_empty_url(self):
        assert post_discord("", "test") is False

    def test_failed_post(self):
        import urllib.error
        with patch("scripts.daily_funnel_report.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("connection refused")
            assert post_discord("https://discord.webhook.invalid", "test") is False

    def test_successful_post(self):
        with patch("scripts.daily_funnel_report.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 204
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp
            assert post_discord("https://discord.webhook.test", "test") is True


class TestDryRun:
    def test_dry_run_prints_to_stdout(self, tmp_dir, capsys):
        import subprocess
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "daily_funnel_report.py"),
             "--dry-run"],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
            timeout=30,
        )
        assert result.returncode == 0
        assert "SlimyAI Daily Funnel Report" in result.stdout
        assert "dry-run" in result.stdout


class TestHoursFlag:
    def test_custom_window(self, now):
        ws = now - timedelta(hours=12)
        result = build_section_header(12, ws, now, "/tmp")
        assert "last 12h" in result


class TestMissingDataSources:
    def test_no_pnl_db(self, tmp_dir, now):
        result = build_section_trading(
            str(tmp_dir / "nonexistent.db"), now - timedelta(hours=24), now)
        assert "No pnl.db found" in result

    def test_no_cron_log(self, tmp_dir, now):
        result = build_section_cron_health(
            str(tmp_dir / "nonexistent.log"), now - timedelta(hours=24), now)
        assert "No cron log found" in result

    def test_no_breaker_state(self, tmp_dir, now):
        result = build_section_provider_health(
            str(tmp_dir / "nonexistent.json"), str(tmp_dir), now - timedelta(hours=24))
        assert "No breaker state" in result

    def test_no_scratchpad_events(self, tmp_dir, now):
        result = build_section_stage_budget(
            str(tmp_dir), now - timedelta(hours=24), {"ai_cascade": 300})
        assert "No stage budget events" in result


class TestGenerateReport:
    def test_full_report_all_sections(self, tmp_dir, now):
        ts = now.isoformat()
        ts_sql = now.strftime("%Y-%m-%d %H:%M:%S")

        scratchpad = tmp_dir / "scratchpad"
        scratchpad.mkdir()
        _write_jsonl(scratchpad / "stage_budget.jsonl", [
            _make_stage_budget_events(ts, "r1", "ai_cascade", 50.0, False, 300),
        ])
        _write_jsonl(scratchpad / "cursor_advance.jsonl", [
            {"ts": ts, "event": "cursor_advance", "markets_processed": 10,
             "total_markets": 400, "rotations_completed": 0},
        ])
        _write_jsonl(scratchpad / "cursor_state.jsonl", [
            {"ts": ts, "event": "cursor_state", "index": 10,
             "list_hash": "abc", "total_rotations": 0, "list_size": 400},
        ])

        breaker = tmp_dir / "circuit_breakers.json"
        breaker.write_text(json.dumps({
            "grok_420": {"state": "CLOSED", "total_calls": 10, "total_failures": 0,
                         "total_opens": 0},
        }))

        cursor = tmp_dir / "cursor.json"
        cursor.write_text(json.dumps({
            "index": 10, "list_hash": "abc", "total_rotations": 0
        }))

        db = tmp_dir / "pnl.db"
        conn = sqlite3.connect(str(db))
        conn.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phase TEXT NOT NULL, ticker TEXT NOT NULL, action TEXT NOT NULL,
            price REAL NOT NULL, size_usd REAL NOT NULL, pnl_usd REAL DEFAULT 0,
            pnl_pct REAL DEFAULT 0, timestamp TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cash REAL NOT NULL, position_value REAL NOT NULL,
            total_value REAL NOT NULL,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.execute("INSERT INTO trades VALUES (1, 'p1', 'TKR', 'BUY', 0.05, 5.0, 0.0, 0.0, ?)", (ts_sql,))
        conn.execute("INSERT INTO equity_snapshots VALUES (1, 100.0, 5.0, 105.0, ?)", (ts_sql,))
        conn.commit()
        conn.close()

        cron_log = tmp_dir / "cron.log"
        cron_log.write_text("")

        report = generate_report(
            window_hours=24,
            repo_path=str(tmp_dir),
            cron_log_path=str(cron_log),
            scratchpad_dir=str(scratchpad),
            cursor_path=str(cursor),
            breaker_path=str(breaker),
            pnl_db_path=str(db),
            stage_budgets_config={"ai_cascade": 300},
        )

        assert "SlimyAI Daily Funnel Report" in report
        assert "Cron Health Summary" in report
        assert "Stage Budget Utilization" in report
        assert "Cascade + Rotation State" in report
        assert "Provider Health" in report
        assert "Trading Activity" in report
        assert "Anomalies and Flags" in report
        assert "ai_cascade" in report
        assert "grok_420" in report
