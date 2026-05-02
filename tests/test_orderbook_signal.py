"""
Tests for signals/orderbook_signal.py and signals/orderbook_store.py

Covers snapshot collection, event detection, Decimal parsing safety,
and SQLite storage.
"""

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from signals.orderbook_signal import (
    _detect_events,
    _parse_decimal_str,
    _parse_orderbook,
    _parse_trades,
    collect_snapshot,
)
from signals.orderbook_store import (
    get_event_snapshots,
    get_snapshots,
    store_snapshot,
    _init_db,
)


class TestParseDecimalStr:
    def test_positive_string(self):
        assert _parse_decimal_str("0.1200") == pytest.approx(0.12)

    def test_zero_string(self):
        assert _parse_decimal_str("0.0000") == pytest.approx(0.0)
        assert _parse_decimal_str("0.0000") is not None

    def test_none_returns_none(self):
        assert _parse_decimal_str(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_decimal_str("") is None

    def test_whitespace_string_returns_none(self):
        assert _parse_decimal_str("   ") is None

    def test_int(self):
        assert _parse_decimal_str(100) == pytest.approx(100.0)

    def test_float(self):
        assert _parse_decimal_str(0.12) == pytest.approx(0.12)

    def test_invalid_string_returns_none(self):
        assert _parse_decimal_str("abc") is None


class TestParseOrderbook:
    def test_empty_orderbook(self):
        result = _parse_orderbook({})
        assert result["yes_bids"] == []
        assert result["no_bids"] == []

    def test_valid_orderbook(self):
        data = {
            "yes_dollars": [["0.5000", "100.00"], ["0.4500", "200.00"]],
            "no_dollars": [["0.6000", "50.00"], ["0.5500", "75.00"]],
        }
        result = _parse_orderbook(data)
        assert len(result["yes_bids"]) == 2
        assert result["yes_bids"][0] == [pytest.approx(0.5), pytest.approx(100.0)]
        assert result["yes_bids"][1] == [pytest.approx(0.45), pytest.approx(200.0)]
        # Should be sorted by price descending
        assert result["yes_bids"][0][0] > result["yes_bids"][1][0]

    def test_orderbook_with_invalid_levels(self):
        data = {
            "yes_dollars": [["0.5000", "100.00"], ["invalid", "200.00"]],
            "no_dollars": [[None, "50.00"]],
        }
        result = _parse_orderbook(data)
        assert len(result["yes_bids"]) == 1
        assert len(result["no_bids"]) == 0

    def test_orderbook_limits_to_top_10(self):
        data = {
            "yes_dollars": [[f"0.{i:02d}00", "10.00"] for i in range(15)],
            "no_dollars": [[f"0.{i:02d}00", "10.00"] for i in range(15)],
        }
        result = _parse_orderbook(data)
        assert len(result["yes_bids"]) == 10
        assert len(result["no_bids"]) == 10


class TestParseTrades:
    def test_empty_trades(self):
        assert _parse_trades([]) == []

    def test_valid_trades(self):
        trades = [
            {
                "trade_id": "t1",
                "count_fp": "50.00",
                "yes_price_dollars": "0.5000",
                "no_price_dollars": "0.5000",
                "taker_side": "yes",
                "created_time": "2026-01-01T00:00:00Z",
            },
            {
                "trade_id": "t2",
                "count_fp": "25.00",
                "yes_price_dollars": "0.5500",
                "taker_side": "no",
                "created_time": "2026-01-01T00:01:00Z",
            },
        ]
        result = _parse_trades(trades)
        assert len(result) == 2
        assert result[0]["trade_id"] == "t1"
        assert result[0]["count_fp"] == pytest.approx(50.0)
        assert result[1]["taker_side"] == "no"

    def test_trade_with_none_count_skipped(self):
        trades = [
            {"trade_id": "t1", "count_fp": None},
            {"trade_id": "t2", "count_fp": "10.00"},
        ]
        result = _parse_trades(trades)
        assert len(result) == 1
        assert result[0]["trade_id"] == "t2"


class TestDetectEvents:
    def test_large_wall_detection(self, monkeypatch):
        monkeypatch.setenv("ORDERBOOK_WALL_MIN_USD", "100")
        # Re-import to pick up env var
        import importlib
        from signals import orderbook_signal
        importlib.reload(orderbook_signal)

        orderbook = {
            "yes_bids": [[0.5, 150.0], [0.45, 50.0]],
            "no_bids": [[0.4, 200.0]],
        }
        events = orderbook_signal._detect_events(
            yes_bid=0.5, yes_ask=0.55, volume_24h=1000.0,
            open_interest=500.0, orderbook=orderbook, trades=[],
        )

        wall_events = [e for e in events if e["type"] == "large_wall"]
        assert len(wall_events) == 2  # 150 and 200 both >= 100

    def test_no_large_wall_below_threshold(self, monkeypatch):
        monkeypatch.setenv("ORDERBOOK_WALL_MIN_USD", "500")
        import importlib
        from signals import orderbook_signal
        importlib.reload(orderbook_signal)

        orderbook = {
            "yes_bids": [[0.5, 100.0]],
            "no_bids": [[0.4, 150.0]],
        }
        events = orderbook_signal._detect_events(
            yes_bid=0.5, yes_ask=0.55, volume_24h=1000.0,
            open_interest=500.0, orderbook=orderbook, trades=[],
        )

        wall_events = [e for e in events if e["type"] == "large_wall"]
        assert len(wall_events) == 0

    def test_large_fill_detection(self, monkeypatch):
        monkeypatch.setenv("ORDERBOOK_LARGE_FILL_USD", "50")
        import importlib
        from signals import orderbook_signal
        importlib.reload(orderbook_signal)

        trades = [
            {"trade_id": "t1", "count_fp": "75.00", "yes_price_dollars": "0.5000", "taker_side": "yes", "created_time": "2026-01-01T00:00:00Z"},
            {"trade_id": "t2", "count_fp": "25.00", "yes_price_dollars": "0.5500", "taker_side": "no", "created_time": "2026-01-01T00:01:00Z"},
        ]
        events = orderbook_signal._detect_events(
            yes_bid=0.5, yes_ask=0.55, volume_24h=1000.0,
            open_interest=500.0, orderbook={"yes_bids": [], "no_bids": []}, trades=trades,
        )

        fill_events = [e for e in events if e["type"] == "large_fill"]
        assert len(fill_events) == 1
        assert fill_events[0]["side"] == "yes"
        assert fill_events[0]["details"]["count"] == pytest.approx(75.0)

    def test_volume_spike_detection(self, monkeypatch):
        monkeypatch.setenv("ORDERBOOK_VOLUME_SPIKE_MULT", "3.0")
        import importlib
        from signals import orderbook_signal
        importlib.reload(orderbook_signal)

        # volume_24h = 100, recent trades sum to 400 = 4x
        trades = [
            {"trade_id": "t1", "count_fp": "200.00"},
            {"trade_id": "t2", "count_fp": "200.00"},
        ]
        events = orderbook_signal._detect_events(
            yes_bid=0.5, yes_ask=0.55, volume_24h=100.0,
            open_interest=500.0, orderbook={"yes_bids": [], "no_bids": []}, trades=trades,
        )

        spike_events = [e for e in events if e["type"] == "volume_spike"]
        assert len(spike_events) == 1
        assert spike_events[0]["details"]["ratio"] == pytest.approx(4.0)

    def test_spread_compression_detection(self, monkeypatch):
        monkeypatch.setenv("ORDERBOOK_SPREAD_COMPRESSION_CENTS", "0.01")
        import importlib
        from signals import orderbook_signal
        importlib.reload(orderbook_signal)

        events = orderbook_signal._detect_events(
            yes_bid=0.50, yes_ask=0.505, volume_24h=1000.0,
            open_interest=500.0, orderbook={"yes_bids": [], "no_bids": []}, trades=[],
        )

        spread_events = [e for e in events if e["type"] == "spread_compression"]
        assert len(spread_events) == 1
        assert spread_events[0]["details"]["spread_cents"] == pytest.approx(0.005)

    def test_no_spread_compression_when_wide(self, monkeypatch):
        monkeypatch.setenv("ORDERBOOK_SPREAD_COMPRESSION_CENTS", "0.01")
        import importlib
        from signals import orderbook_signal
        importlib.reload(orderbook_signal)

        events = orderbook_signal._detect_events(
            yes_bid=0.50, yes_ask=0.60, volume_24h=1000.0,
            open_interest=500.0, orderbook={"yes_bids": [], "no_bids": []}, trades=[],
        )

        spread_events = [e for e in events if e["type"] == "spread_compression"]
        assert len(spread_events) == 0

    def test_depth_imbalance_detection(self, monkeypatch):
        monkeypatch.setenv("ORDERBOOK_DEPTH_IMBALANCE_RATIO", "3.0")
        import importlib
        from signals import orderbook_signal
        importlib.reload(orderbook_signal)

        orderbook = {
            "yes_bids": [[0.5, 300.0], [0.45, 100.0]],
            "no_bids": [[0.4, 50.0]],
        }
        events = orderbook_signal._detect_events(
            yes_bid=0.5, yes_ask=0.55, volume_24h=1000.0,
            open_interest=500.0, orderbook=orderbook, trades=[],
        )

        imbalance_events = [e for e in events if e["type"] == "depth_imbalance"]
        assert len(imbalance_events) == 1
        assert imbalance_events[0]["side"] == "yes"
        # ratio = 400 / 50 = 8.0

    def test_no_events_when_nothing_notable(self, monkeypatch):
        monkeypatch.setenv("ORDERBOOK_WALL_MIN_USD", "1000")
        monkeypatch.setenv("ORDERBOOK_LARGE_FILL_USD", "1000")
        monkeypatch.setenv("ORDERBOOK_VOLUME_SPIKE_MULT", "100.0")
        import importlib
        from signals import orderbook_signal
        importlib.reload(orderbook_signal)

        orderbook = {
            "yes_bids": [[0.5, 50.0]],
            "no_bids": [[0.4, 50.0]],
        }
        events = orderbook_signal._detect_events(
            yes_bid=0.50, yes_ask=0.60, volume_24h=1000.0,
            open_interest=500.0, orderbook=orderbook, trades=[],
        )

        assert len(events) == 0


class TestOrderbookStore:
    def test_init_db_creates_tables(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            _init_db(db_path)
            conn = sqlite3.connect(db_path)
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = [t[0] for t in tables]
            assert "orderbook_snapshots" in table_names
            conn.close()
        finally:
            os.unlink(db_path)

    def test_store_and_retrieve(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            snapshot = {
                "timestamp_utc": "2026-01-01T00:00:00Z",
                "venue": "kalshi",
                "ticker": "KXETHY-TEST",
                "question": "Test market",
                "yes_bid": 0.5,
                "yes_ask": 0.55,
                "spread_cents": 0.05,
                "volume_24h": 1000.0,
                "open_interest": 500.0,
                "orderbook": {"yes_bids": [[0.5, 100.0]], "no_bids": [[0.4, 50.0]]},
                "recent_trades": [{"trade_id": "t1", "count_fp": 10.0}],
                "events": [{"type": "large_wall", "side": "yes"}],
            }
            row_id = store_snapshot(db_path=db_path, snapshot=snapshot)
            assert row_id > 0

            rows = get_snapshots(db_path=db_path, ticker="KXETHY-TEST")
            assert len(rows) == 1
            assert rows[0]["ticker"] == "KXETHY-TEST"
            assert rows[0]["event_count"] == 1
        finally:
            os.unlink(db_path)

    def test_get_event_snapshots_only_returns_with_events(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            snapshot_with = {
                "timestamp_utc": "2026-01-01T00:00:00Z",
                "venue": "kalshi",
                "ticker": "KXETHY-A",
                "question": "Test A",
                "yes_bid": 0.5,
                "yes_ask": 0.55,
                "spread_cents": 0.05,
                "volume_24h": 1000.0,
                "open_interest": 500.0,
                "orderbook": {},
                "recent_trades": [],
                "events": [{"type": "large_wall"}],
            }
            snapshot_without = {
                "timestamp_utc": "2026-01-01T00:01:00Z",
                "venue": "kalshi",
                "ticker": "KXETHY-B",
                "question": "Test B",
                "yes_bid": 0.5,
                "yes_ask": 0.55,
                "spread_cents": 0.05,
                "volume_24h": 1000.0,
                "open_interest": 500.0,
                "orderbook": {},
                "recent_trades": [],
                "events": [],
            }
            store_snapshot(db_path=db_path, snapshot=snapshot_with)
            store_snapshot(db_path=db_path, snapshot=snapshot_without)

            event_rows = get_event_snapshots(db_path=db_path)
            assert len(event_rows) == 1
            assert event_rows[0]["ticker"] == "KXETHY-A"
        finally:
            os.unlink(db_path)


class TestCollectSnapshotIntegration:
    def test_collect_snapshot_no_creds_returns_empty(self, monkeypatch):
        monkeypatch.setenv("KALSHI_KEY", "")
        snapshot = collect_snapshot(ticker="KXETHY-TEST")
        assert snapshot == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
