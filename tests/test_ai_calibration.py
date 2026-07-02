import sqlite3

import pytest

from providers import ai_calibration


def _make_db(path=None):
    conn = sqlite3.connect(path or ":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ai_probability REAL,
            status TEXT,
            realized_pnl_usd REAL,
            pnl_usd REAL,
            settled_price REAL
        )
        """
    )
    return conn


def _insert_rows(conn, prob, count, wins):
    for idx in range(count):
        won = idx < wins
        conn.execute(
            """
            INSERT INTO trades (ai_probability, status, realized_pnl_usd, pnl_usd, settled_price)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                prob,
                "settled_won" if won else "settled_lost",
                0.10 if won else -0.10,
                0.10 if won else -0.10,
                1.0 if won else 0.0,
            ),
        )
    conn.commit()


def _add_filler(conn, count=50):
    _insert_rows(conn, 0.55, count, count // 2)


def test_build_calibration_from_mock_db():
    conn = _make_db()
    _insert_rows(conn, 0.82, 10, 4)
    _insert_rows(conn, 0.35, 10, 6)
    _add_filler(conn, 40)

    table = ai_calibration.build_calibration_table(conn)

    assert table["total_calibration_trades"] == 60
    assert table["enough_data"] is True
    assert table["buckets"]["0.8-0.9"]["count"] == 10
    assert table["buckets"]["0.8-0.9"]["wins"] == 4
    assert table["buckets"]["0.8-0.9"]["actual_rate"] == pytest.approx(0.40)
    assert table["overall_bias"] is not None


def test_calibrate_overconfident():
    conn = _make_db()
    _insert_rows(conn, 0.82, 10, 4)
    _add_filler(conn, 40)

    table = ai_calibration.build_calibration_table(conn)

    assert ai_calibration.calibrate_probability(0.80, table) == pytest.approx(0.40)


def test_calibrate_underconfident():
    conn = _make_db()
    _insert_rows(conn, 0.35, 20, 11)
    _add_filler(conn, 30)

    table = ai_calibration.build_calibration_table(conn)

    assert ai_calibration.calibrate_probability(0.30, table) == pytest.approx(0.55)


def test_fallback_insufficient_data():
    conn = _make_db()
    _insert_rows(conn, 0.82, 10, 4)

    table = ai_calibration.build_calibration_table(conn)

    assert table["total_calibration_trades"] == 10
    assert table["enough_data"] is False
    assert ai_calibration.calibrate_probability(0.80, table) == pytest.approx(0.65)


def test_fallback_sparse_bucket():
    conn = _make_db()
    _insert_rows(conn, 0.15, 2, 2)
    _insert_rows(conn, 0.25, 5, 3)
    _add_filler(conn, 45)

    table = ai_calibration.build_calibration_table(conn)

    assert table["buckets"]["0.1-0.2"]["count"] == 2
    assert ai_calibration.nearest_bucket_name(0.15, table) == "0.2-0.3"
    assert ai_calibration.calibrate_probability(0.15, table) == pytest.approx(0.60)


def test_cache_rebuild(tmp_path, monkeypatch):
    db_path = tmp_path / "pnl.db"
    conn = _make_db(db_path)
    _insert_rows(conn, 0.75, 10, 8)
    _add_filler(conn, 40)
    conn.close()

    ai_calibration.clear_cache()
    monkeypatch.setattr(ai_calibration, "_now", lambda: 1000.0)
    first = ai_calibration.build_calibration_table(db_path)
    assert ai_calibration.calibrate_probability(0.75, first) == pytest.approx(0.80)

    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM trades")
    conn.commit()
    _insert_rows(conn, 0.75, 10, 2)
    _add_filler(conn, 40)
    conn.close()

    second = ai_calibration.build_calibration_table(db_path)
    assert ai_calibration.calibrate_probability(0.75, second) == pytest.approx(0.80)

    monkeypatch.setattr(ai_calibration, "_now", lambda: 1000.0 + ai_calibration.CACHE_TTL_SECONDS + 1)
    rebuilt = ai_calibration.build_calibration_table(db_path)
    assert ai_calibration.calibrate_probability(0.75, rebuilt) == pytest.approx(0.20)
