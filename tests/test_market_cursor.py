#!/usr/bin/env python3
"""
Tests for core/market_cursor.py — Rotating market cursor for cascade fairness.
Reliability Phase 1, Module 1.5.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.market_cursor import (
    MarketCursor,
    load_cursor,
    save_cursor,
    compute_list_hash,
    rotate_markets,
    advance_cursor,
)


class TestLoadCursor:
    def test_missing_file_returns_default(self, tmp_path):
        cursor = load_cursor(tmp_path / "nonexistent.json")
        assert cursor.index == 0
        assert cursor.list_hash == ""
        assert cursor.total_rotations == 0

    def test_corrupt_file_returns_default(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json{{{")
        cursor = load_cursor(bad_file)
        assert cursor.index == 0
        assert cursor.list_hash == ""
        assert cursor.total_rotations == 0

    def test_empty_file_returns_default(self, tmp_path):
        empty_file = tmp_path / "empty.json"
        empty_file.write_text("")
        cursor = load_cursor(empty_file)
        assert cursor.index == 0

    def test_valid_file_loads_correctly(self, tmp_path):
        cursor_file = tmp_path / "cursor.json"
        data = {
            "index": 42,
            "list_hash": "abc123",
            "last_updated": "2026-01-01T00:00:00+00:00",
            "total_rotations": 5,
        }
        cursor_file.write_text(json.dumps(data))
        cursor = load_cursor(cursor_file)
        assert cursor.index == 42
        assert cursor.list_hash == "abc123"
        assert cursor.total_rotations == 5

    def test_partial_file_uses_defaults(self, tmp_path):
        cursor_file = tmp_path / "partial.json"
        cursor_file.write_text(json.dumps({"index": 10}))
        cursor = load_cursor(cursor_file)
        assert cursor.index == 10
        assert cursor.list_hash == ""
        assert cursor.total_rotations == 0


class TestSaveCursor:
    def test_save_and_load_roundtrip(self, tmp_path):
        cursor_file = tmp_path / "cursor.json"
        original = MarketCursor(index=7, list_hash="deadbeef", total_rotations=3)
        save_cursor(original, cursor_file)

        loaded = load_cursor(cursor_file)
        assert loaded.index == 7
        assert loaded.list_hash == "deadbeef"
        assert loaded.total_rotations == 3
        assert loaded.last_updated is not None

    def test_atomic_write_uses_tmp_then_rename(self, tmp_path):
        cursor_file = tmp_path / "cursor.json"
        save_cursor(MarketCursor(index=1), cursor_file)
        assert cursor_file.exists()
        tmp_check = Path(str(cursor_file) + ".tmp")
        assert not tmp_check.exists()

    def test_save_creates_parent_dirs(self, tmp_path):
        deep_path = tmp_path / "a" / "b" / "c" / "cursor.json"
        save_cursor(MarketCursor(index=99), deep_path)
        assert deep_path.exists()
        loaded = load_cursor(deep_path)
        assert loaded.index == 99

    def test_overwrite_existing(self, tmp_path):
        cursor_file = tmp_path / "cursor.json"
        save_cursor(MarketCursor(index=1), cursor_file)
        save_cursor(MarketCursor(index=2), cursor_file)
        loaded = load_cursor(cursor_file)
        assert loaded.index == 2


class TestComputeListHash:
    def test_deterministic(self):
        h1 = compute_list_hash(["AAPL", "GOOG", "MSFT"])
        h2 = compute_list_hash(["AAPL", "GOOG", "MSFT"])
        assert h1 == h2

    def test_order_independent(self):
        h1 = compute_list_hash(["AAPL", "GOOG", "MSFT"])
        h2 = compute_list_hash(["MSFT", "AAPL", "GOOG"])
        assert h1 == h2

    def test_different_inputs_different_hash(self):
        h1 = compute_list_hash(["AAPL", "GOOG"])
        h2 = compute_list_hash(["AAPL", "TSLA"])
        assert h1 != h2

    def test_empty_list(self):
        h = compute_list_hash([])
        assert h == ""

    def test_single_item(self):
        h = compute_list_hash(["AAPL"])
        assert len(h) == 64

    def test_hash_is_sha256_hex(self):
        h = compute_list_hash(["TEST"])
        assert all(c in "0123456789abcdef" for c in h)
        assert len(h) == 64


class TestRotateMarkets:
    def _make_markets(self, n):
        return [{"ticker": f"MKT-{i:03d}", "id": f"MKT-{i:03d}"} for i in range(n)]

    def test_cursor_at_0_returns_original_order(self):
        markets = self._make_markets(5)
        cursor = MarketCursor(index=0, list_hash=compute_list_hash(["MKT-000", "MKT-001", "MKT-002", "MKT-003", "MKT-004"]))
        rotated, updated, hash_changed = rotate_markets(markets, cursor)
        assert [m["ticker"] for m in rotated] == [f"MKT-{i:03d}" for i in range(5)]
        assert hash_changed is False

    def test_cursor_at_3_rotates(self):
        markets = self._make_markets(5)
        tickers = [m["ticker"] for m in markets]
        cursor = MarketCursor(index=3, list_hash=compute_list_hash(tickers))
        rotated, updated, hash_changed = rotate_markets(markets, cursor)
        expected = [f"MKT-{i:03d}" for i in [3, 4, 0, 1, 2]]
        assert [m["ticker"] for m in rotated] == expected

    def test_cursor_at_10_wraps_correctly(self):
        markets = self._make_markets(20)
        tickers = [m["ticker"] for m in markets]
        cursor = MarketCursor(index=10, list_hash=compute_list_hash(tickers))
        rotated, updated, hash_changed = rotate_markets(markets, cursor)
        expected = [f"MKT-{i:03d}" for i in range(10, 20)] + [f"MKT-{i:03d}" for i in range(10)]
        assert [m["ticker"] for m in rotated] == expected

    def test_hash_mismatch_resets_to_0(self):
        markets = self._make_markets(5)
        cursor = MarketCursor(index=3, list_hash="old_hash_value")
        rotated, updated, hash_changed = rotate_markets(markets, cursor)
        assert updated.index == 0
        assert hash_changed is True
        assert [m["ticker"] for m in rotated] == [f"MKT-{i:03d}" for i in range(5)]

    def test_empty_hash_does_not_trigger_reset(self):
        markets = self._make_markets(5)
        cursor = MarketCursor(index=3, list_hash="")
        rotated, updated, hash_changed = rotate_markets(markets, cursor)
        assert updated.index == 3
        assert hash_changed is False

    def test_empty_market_list(self):
        cursor = MarketCursor(index=0, list_hash="")
        rotated, updated, hash_changed = rotate_markets([], cursor)
        assert rotated == []
        assert hash_changed is False

    def test_cursor_index_exceeds_list_size(self):
        markets = self._make_markets(5)
        cursor = MarketCursor(index=100, list_hash=compute_list_hash([m["ticker"] for m in markets]))
        rotated, updated, hash_changed = rotate_markets(markets, cursor)
        assert updated.index == 0
        assert [m["ticker"] for m in rotated] == [f"MKT-{i:03d}" for i in range(5)]

    def test_preserves_all_market_data(self):
        markets = [{"ticker": "A", "price": 0.5}, {"ticker": "B", "price": 0.7}]
        cursor = MarketCursor(index=1, list_hash=compute_list_hash(["A", "B"]))
        rotated, _, _ = rotate_markets(markets, cursor)
        assert rotated[0] == {"ticker": "B", "price": 0.7}
        assert rotated[1] == {"ticker": "A", "price": 0.5}

    def test_does_not_advance_cursor(self):
        markets = self._make_markets(10)
        cursor = MarketCursor(index=5, list_hash=compute_list_hash([m["ticker"] for m in markets]))
        _, updated, _ = rotate_markets(markets, cursor)
        assert updated.index == 5


class TestAdvanceCursor:
    def test_advance_within_bounds(self):
        cursor = MarketCursor(index=0, list_hash="abc", total_rotations=0)
        advanced = advance_cursor(cursor, 5, 20)
        assert advanced.index == 5
        assert advanced.total_rotations == 0

    def test_wrap_at_end(self):
        cursor = MarketCursor(index=18, list_hash="abc", total_rotations=0)
        advanced = advance_cursor(cursor, 5, 20)
        assert advanced.index == 3
        assert advanced.total_rotations == 1

    def test_multiple_wraps(self):
        cursor = MarketCursor(index=0, list_hash="abc", total_rotations=0)
        advanced = advance_cursor(cursor, 45, 20)
        assert advanced.index == 5
        assert advanced.total_rotations == 2

    def test_advance_zero(self):
        cursor = MarketCursor(index=7, list_hash="abc", total_rotations=2)
        advanced = advance_cursor(cursor, 0, 20)
        assert advanced.index == 7
        assert advanced.total_rotations == 2

    def test_advance_exact_to_boundary(self):
        cursor = MarketCursor(index=0, list_hash="abc", total_rotations=0)
        advanced = advance_cursor(cursor, 20, 20)
        assert advanced.index == 0
        assert advanced.total_rotations == 1

    def test_zero_total_markets(self):
        cursor = MarketCursor(index=0, list_hash="abc", total_rotations=0)
        advanced = advance_cursor(cursor, 5, 0)
        assert advanced.index == 0
        assert advanced.total_rotations == 0

    def test_preserves_hash(self):
        cursor = MarketCursor(index=0, list_hash="deadbeef", total_rotations=0)
        advanced = advance_cursor(cursor, 10, 20)
        assert advanced.list_hash == "deadbeef"

    def test_cumulative_rotations(self):
        cursor = MarketCursor(index=0, list_hash="abc", total_rotations=5)
        advanced = advance_cursor(cursor, 22, 20)
        assert advanced.total_rotations == 6


class TestIntegration:
    def test_three_simulated_cron_runs(self, tmp_path):
        cursor_file = tmp_path / "cursor.json"
        total_markets = 10
        markets_per_run = 4

        markets = [{"ticker": f"T-{i}", "id": f"T-{i}"} for i in range(total_markets)]
        tickers = [m["ticker"] for m in markets]
        list_hash = compute_list_hash(tickers)

        cursor_states = []
        for run in range(3):
            cursor = load_cursor(cursor_file)
            result = rotate_markets(markets, cursor)
            rotated, updated_cursor, hash_changed = result

            cursor_states.append({
                "run": run,
                "index_before": cursor.index,
                "rotated_order": [m["ticker"] for m in rotated],
                "hash_changed": hash_changed,
            })

            cursor_after = advance_cursor(updated_cursor, markets_per_run, total_markets)
            save_cursor(cursor_after, cursor_file)

        assert cursor_states[0]["index_before"] == 0
        assert cursor_states[0]["rotated_order"] == [f"T-{i}" for i in range(10)]
        assert cursor_states[1]["index_before"] == 4
        assert cursor_states[1]["rotated_order"] == [f"T-{i}" for i in range(4, 10)] + [f"T-{i}" for i in range(4)]
        assert cursor_states[2]["index_before"] == 8
        assert cursor_states[2]["rotated_order"] == [f"T-{i}" for i in range(8, 10)] + [f"T-{i}" for i in range(8)]

        final = load_cursor(cursor_file)
        assert final.index == 2
        assert final.total_rotations == 1

    def test_hash_change_resets_cursor(self, tmp_path):
        cursor_file = tmp_path / "cursor.json"
        save_cursor(MarketCursor(index=5, list_hash="oldhash", total_rotations=3), cursor_file)

        new_markets = [{"ticker": f"NEW-{i}", "id": f"NEW-{i}"} for i in range(8)]
        cursor = load_cursor(cursor_file)
        rotated, updated, hash_changed = rotate_markets(new_markets, cursor)

        assert hash_changed is True
        assert updated.index == 0
        assert updated.total_rotations == 3

    def test_full_rotation_cycle(self, tmp_path):
        cursor_file = tmp_path / "cursor.json"
        total = 6
        per_run = 3
        markets = [{"ticker": f"M-{i}", "id": f"M-{i}"} for i in range(total)]

        all_seen = set()
        for _ in range(4):
            cursor = load_cursor(cursor_file)
            rotated, updated, _ = rotate_markets(markets, cursor)
            for m in rotated[:per_run]:
                all_seen.add(m["ticker"])
            cursor_after = advance_cursor(updated, per_run, total)
            save_cursor(cursor_after, cursor_file)

        assert all_seen == {f"M-{i}" for i in range(total)}

    def test_scratchpad_events_logged(self, tmp_path):
        sp_dir = tmp_path / "scratchpad"
        sp_dir.mkdir()

        cursor_file = tmp_path / "cursor.json"
        markets = [{"ticker": f"T-{i}", "id": f"T-{i}"} for i in range(5)]
        cursor = load_cursor(cursor_file)
        rotated, updated, hash_changed = rotate_markets(markets, cursor)

        event = {
            "event_type": "cursor_state",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cron_run_id": "test-run-001",
            "index": updated.index,
            "list_hash": updated.list_hash,
            "total_rotations": updated.total_rotations,
            "list_size": len(markets),
        }
        with open(sp_dir / "cursor_state.jsonl", "a") as f:
            f.write(json.dumps(event) + "\n")

        cursor_after = advance_cursor(updated, 3, 5)
        event2 = {
            "event_type": "cursor_advance",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cron_run_id": "test-run-001",
            "index_before": updated.index,
            "index_after": cursor_after.index,
            "markets_processed": 3,
            "rotations_completed": cursor_after.total_rotations,
            "hash_changed": hash_changed,
        }
        with open(sp_dir / "cursor_advance.jsonl", "a") as f:
            f.write(json.dumps(event2) + "\n")

        state_events = [json.loads(l) for l in (sp_dir / "cursor_state.jsonl").read_text().strip().split("\n")]
        assert len(state_events) == 1
        assert state_events[0]["event_type"] == "cursor_state"
        assert state_events[0]["index"] == 0

        advance_events = [json.loads(l) for l in (sp_dir / "cursor_advance.jsonl").read_text().strip().split("\n")]
        assert len(advance_events) == 1
        assert advance_events[0]["index_before"] == 0
        assert advance_events[0]["index_after"] == 3
        assert advance_events[0]["markets_processed"] == 3
        assert advance_events[0]["hash_changed"] is False

    def test_cursor_reset_scratchpad_event(self, tmp_path):
        sp_dir = tmp_path / "scratchpad"
        sp_dir.mkdir()

        cursor_file = tmp_path / "cursor.json"
        save_cursor(MarketCursor(index=5, list_hash="oldhash", total_rotations=3), cursor_file)

        new_markets = [{"ticker": f"X-{i}", "id": f"X-{i}"} for i in range(5)]
        cursor = load_cursor(cursor_file)
        rotated, updated, hash_changed = rotate_markets(new_markets, cursor)

        if hash_changed:
            event = {
                "event_type": "cursor_reset",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "cron_run_id": "test-reset-001",
                "old_hash": cursor.list_hash,
                "new_hash": updated.list_hash,
                "reason": "list_changed",
            }
            with open(sp_dir / "cursor_reset.jsonl", "a") as f:
                f.write(json.dumps(event) + "\n")

        reset_events = [json.loads(l) for l in (sp_dir / "cursor_reset.jsonl").read_text().strip().split("\n")]
        assert len(reset_events) == 1
        assert reset_events[0]["event_type"] == "cursor_reset"
        assert reset_events[0]["old_hash"] == "oldhash"
        assert reset_events[0]["reason"] == "list_changed"


class TestEdgeCases:
    def test_single_market(self):
        markets = [{"ticker": "ONLY", "id": "ONLY"}]
        cursor = MarketCursor(index=0, list_hash=compute_list_hash(["ONLY"]))
        rotated, updated, _ = rotate_markets(markets, cursor)
        assert len(rotated) == 1
        assert rotated[0]["ticker"] == "ONLY"

        advanced = advance_cursor(updated, 1, 1)
        assert advanced.index == 0
        assert advanced.total_rotations == 1

    def test_markets_without_ticker_use_id(self):
        markets = [{"id": "A"}, {"id": "B"}]
        cursor = MarketCursor(index=1, list_hash=compute_list_hash(["A", "B"]))
        rotated, _, _ = rotate_markets(markets, cursor)
        assert rotated[0]["id"] == "B"
        assert rotated[1]["id"] == "A"

    def test_concurrent_save_safety(self, tmp_path):
        import threading
        cursor_file = tmp_path / "cursor.json"
        save_cursor(MarketCursor(index=0), cursor_file)
        errors = []

        def writer(idx):
            try:
                for i in range(10):
                    save_cursor(MarketCursor(index=idx * 10 + i, list_hash=f"hash-{idx}"), cursor_file)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        loaded = load_cursor(cursor_file)
        assert loaded.index >= 0
