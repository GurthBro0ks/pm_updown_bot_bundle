"""Deterministic local-only accumulated-ledger benchmark helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, Sequence

from .capture import CaptureConfig, DEFAULT_CAPTURE_MAX_PER_RUN, DEFAULT_SQLITE_TIMEOUT_MS
from .migrations import validate_migrations
from .runtime_adapter import CandidateCaptureRuntime
from .store import CandidateLedger


DEFAULT_SCALES = (0, 1_000, 10_000, 50_000)
OPTIONAL_SCALE = 100_000
DEFAULT_REPEATS = 5
SYNTHETIC_TIMESTAMP = "2026-07-16T00:00:00Z"
SYNTHETIC_GIT_COMMIT = "35622c7d02ae6dc65ea49830b4b76f992d77b166"
ROOT = Path(__file__).resolve().parents[2]
STATUS_CLI = ROOT / "scripts" / "candidate_capture_status_redacted.py"
VALIDATE_CLI = ROOT / "scripts" / "candidate_ledger_validate.py"


@dataclass(frozen=True)
class TimingSummary:
    samples_ms: list[float]
    minimum_ms: float
    maximum_ms: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def timing_summary(values: Sequence[float]) -> dict[str, Any]:
    rounded = [round(value, 3) for value in values]
    summary = TimingSummary(
        samples_ms=rounded,
        minimum_ms=round(min(values), 3),
        maximum_ms=round(max(values), 3),
        mean_ms=round(statistics.fmean(values), 3),
        p50_ms=round(_percentile(values, 0.50), 3),
        p95_ms=round(_percentile(values, 0.95), 3),
        p99_ms=round(_percentile(values, 0.99), 3),
    )
    return asdict(summary)


def validate_benchmark_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if resolved == temporary_root or temporary_root not in resolved.parents:
        raise ValueError("benchmark databases must use a dedicated temporary directory")
    if not resolved.is_dir() or resolved.is_symlink():
        raise ValueError("benchmark directory must be an existing non-symlink directory")
    return resolved


def _market(index: int) -> dict[str, object]:
    ticker = f"SYNTHETIC-BENCH-{index:09d}"
    return {
        "id": ticker,
        "ticker": ticker,
        "title": "Synthetic accumulated-ledger candidate",
        "close_time": "2026-07-17T00:00:00Z",
        "series_ticker": "SYNTHETIC-BENCH",
        "series_category": "economics",
        "_category": "economics",
        "_ai_tier": "synthetic",
        "_yes_bid_price": 0.49,
        "_yes_ask_price": 0.51,
    }


def _runtime(database: Path, *, run_id: str, max_per_run: int) -> CandidateCaptureRuntime:
    return CandidateCaptureRuntime(
        mode="shadow",
        run_id=run_id,
        run_timestamp=SYNTHETIC_TIMESTAMP,
        git_commit=SYNTHETIC_GIT_COMMIT,
        config=CaptureConfig(
            enabled=True,
            database_path=str(database),
            max_per_run=max_per_run,
            sqlite_timeout_ms=DEFAULT_SQLITE_TIMEOUT_MS,
        ),
    )


def _prepare_capture_batch(
    database: Path,
    *,
    start_index: int,
    candidate_count: int,
    run_id: str,
    max_per_run: int = DEFAULT_CAPTURE_MAX_PER_RUN,
) -> CandidateCaptureRuntime:
    runtime = _runtime(database, run_id=run_id, max_per_run=max_per_run)
    for index in range(start_index, start_index + candidate_count):
        rejected = index % 2 == 0
        added = runtime.record_evaluation(
            market=_market(index),
            observed_price=0.50,
            ai_prior=0.70,
            fallback_prior_used=False,
            raw_edge=40.0,
            fee_adjusted_edge=35.0,
            required_threshold=3.0,
            rejection_reason="edge_below_threshold" if rejected else None,
            gate_failure_kinds=["edge_below_threshold"] if rejected else [],
            order_intent_created=not rejected,
            intent_price=0.49 if not rejected else None,
            maker_assumption="maker" if not rejected else "unknown",
            expected_value=35.0,
        )
        if not added:
            raise RuntimeError("deterministic synthetic candidate was not buffered")
    return runtime


def _supplement_order_events(database: Path, candidate_count: int) -> int:
    with CandidateLedger(database, timeout_ms=DEFAULT_SQLITE_TIMEOUT_MS) as ledger:
        rows = ledger.connection.execute(
            "SELECT candidate_id,payload_json FROM candidate_events "
            "WHERE event_type='candidate_observed' ORDER BY sequence DESC LIMIT ?",
            (candidate_count,),
        ).fetchall()
        events: list[dict[str, object]] = []
        for row in rows:
            candidate = json.loads(row["payload_json"])
            reference = candidate["decision"]["internal_order_reference"]
            if reference is None:
                continue
            candidate_id = row["candidate_id"]
            events.extend(
                [
                    {
                        "candidate_id": candidate_id,
                        "event_type": "order_attempted",
                        "event_timestamp": SYNTHETIC_TIMESTAMP,
                        "payload": {"internal_order_reference": reference},
                    },
                    {
                        "candidate_id": candidate_id,
                        "event_type": "order_result",
                        "event_timestamp": SYNTHETIC_TIMESTAMP,
                        "payload": {
                            "internal_order_reference": reference,
                            "placed": False,
                            "rejection_reason": "synthetic benchmark",
                        },
                    },
                ]
            )
        if events:
            ledger.append_events(events)
        return len(events)


def populate_history(database: Path, candidate_count: int) -> dict[str, Any]:
    started = time.perf_counter()
    transaction_count = 0
    event_count = 0
    for start in range(0, candidate_count, DEFAULT_CAPTURE_MAX_PER_RUN):
        count = min(DEFAULT_CAPTURE_MAX_PER_RUN, candidate_count - start)
        runtime = _prepare_capture_batch(
            database,
            start_index=start,
            candidate_count=count,
            run_id=f"history-{start // DEFAULT_CAPTURE_MAX_PER_RUN:06d}",
        )
        status = runtime.flush()
        if status.status != "PASS":
            raise RuntimeError(f"history population failed with status {status.status}")
        transaction_count += 1
        event_count += status.written_count
        supplemental = _supplement_order_events(database, count)
        if supplemental:
            transaction_count += 1
            event_count += supplemental
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {
        "candidate_count": candidate_count,
        "event_count": event_count,
        "transaction_count": transaction_count,
        "batch_candidates": DEFAULT_CAPTURE_MAX_PER_RUN,
        "wall_ms": round(elapsed_ms, 3),
        "average_ms_per_candidate": round(elapsed_ms / candidate_count, 6) if candidate_count else 0.0,
        "average_ms_per_event": round(elapsed_ms / event_count, 6) if event_count else 0.0,
    }


def _measure_append(
    database: Path,
    *,
    start_index: int,
    repeat: int,
    candidate_count: int,
) -> tuple[float, float, int, str]:
    overall_started = time.perf_counter()
    runtime = _prepare_capture_batch(
        database,
        start_index=start_index,
        candidate_count=candidate_count,
        run_id=f"measured-{start_index}-{repeat}",
    )
    flush_started = time.perf_counter()
    status = runtime.flush()
    flush_ms = (time.perf_counter() - flush_started) * 1000.0
    overall_ms = (time.perf_counter() - overall_started) * 1000.0
    if status.status != "PASS":
        raise RuntimeError(f"measured append failed with status {status.status}")
    return overall_ms, flush_ms, status.written_count, runtime.run_id


def _measure_status_cli(database: Path, repeats: int) -> dict[str, Any]:
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        result = subprocess.run(
            [sys.executable, str(STATUS_CLI), "--database", str(database)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        samples.append((time.perf_counter() - started) * 1000.0)
        if result.returncode != 0 or "CANDIDATE_CAPTURE_STATUS=PASS" not in result.stdout:
            raise RuntimeError("redacted status CLI failed")
        if "Synthetic accumulated-ledger candidate" in result.stdout:
            raise RuntimeError("status CLI exposed a synthetic candidate value")
    return timing_summary(samples)


def _measure_validation(
    database: Path,
    repeats: int,
    *,
    deep: bool,
) -> dict[str, Any]:
    samples: list[float] = []
    checked = 0
    for _ in range(repeats):
        started = time.perf_counter()
        command = [
            sys.executable,
            str(VALIDATE_CLI),
            "--database",
            str(database),
        ]
        if deep:
            command.append("--deep")
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
        samples.append((time.perf_counter() - started) * 1000.0)
        if completed.returncode != 0:
            raise RuntimeError("accumulated ledger validation failed")
        result = json.loads(completed.stdout)
        expected_mode = "deep" if deep else "quick"
        if result["VALIDATION_MODE"] != expected_mode:
            raise RuntimeError("validation CLI reported an unexpected mode")
        if "Synthetic accumulated-ledger candidate" in completed.stdout:
            raise RuntimeError("validation CLI exposed a synthetic candidate value")
        checked = int(result["events_checked"])
    output = timing_summary(samples)
    output["events_checked"] = checked
    output["validation_mode"] = "deep" if deep else "quick"
    output["historical_payloads_fully_validated"] = deep
    return output


def _measure_populated_migration(database: Path) -> dict[str, Any]:
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        event_count_before = int(
            connection.execute("SELECT COUNT(*) FROM candidate_events").fetchone()[0]
        )
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DROP INDEX candidate_event_type_time")
        connection.execute("DELETE FROM schema_migrations WHERE version=2")
        connection.commit()
    finally:
        connection.close()

    started = time.perf_counter()
    with CandidateLedger.initialize(
        database, timeout_ms=DEFAULT_SQLITE_TIMEOUT_MS
    ) as ledger:
        validation = validate_migrations(ledger.connection)
        event_count_after = int(
            ledger.connection.execute("SELECT COUNT(*) FROM candidate_events").fetchone()[0]
        )
    return {
        "wall_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "from_version": 1,
        "to_version": 2,
        "event_count_before": event_count_before,
        "event_count_after": event_count_after,
        "history_preserved": event_count_before == event_count_after,
        "append_only_enforced": bool(validation["append_only_enforced"]),
        "required_indexes_present": bool(validation["required_indexes_present"]),
        "valid": bool(validation["valid"]),
    }


def _database_profile(database: Path) -> dict[str, Any]:
    with CandidateLedger.open_read_only(database) as ledger:
        summary = ledger.summary()
        page_size = int(ledger.connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(ledger.connection.execute("PRAGMA page_count").fetchone()[0])
        index_bytes: dict[str, int] = {}
        try:
            index_bytes = {
                str(row["name"]): int(row["bytes"])
                for row in ledger.connection.execute(
                    "SELECT name,SUM(pgsize) AS bytes FROM dbstat "
                    "WHERE name IN ('sqlite_autoindex_candidate_events_1','one_candidate_snapshot','candidate_event_history','candidate_event_type_time') "
                    "GROUP BY name ORDER BY name"
                )
            }
        except sqlite3.DatabaseError:
            index_bytes = {}
    database_bytes = database.stat().st_size
    event_count = int(summary["event_count"])
    return {
        "database_bytes": database_bytes,
        "page_allocated_bytes": page_size * page_count,
        "bytes_per_event": round(database_bytes / event_count, 3) if event_count else 0.0,
        "candidate_count": int(summary["candidate_count"]),
        "event_count": event_count,
        "event_counts": summary["event_counts"],
        "index_bytes": index_bytes,
        "total_index_bytes": sum(index_bytes.values()),
    }


def _check_append_only(database: Path) -> dict[str, bool]:
    outcomes = {"update_rejected": False, "delete_rejected": False}
    with CandidateLedger(database, timeout_ms=DEFAULT_SQLITE_TIMEOUT_MS) as ledger:
        for statement, name in (
            ("UPDATE candidate_events SET event_type=event_type WHERE sequence=(SELECT MIN(sequence) FROM candidate_events)", "update_rejected"),
            ("DELETE FROM candidate_events WHERE sequence=(SELECT MIN(sequence) FROM candidate_events)", "delete_rejected"),
        ):
            try:
                ledger.connection.execute(statement)
            except sqlite3.IntegrityError as exc:
                outcomes[name] = "append-only" in str(exc)
            finally:
                if ledger.connection.in_transaction:
                    ledger.connection.rollback()
    return outcomes


def _check_lock_isolation(database: Path, start_index: int) -> dict[str, Any]:
    runtime = _prepare_capture_batch(
        database,
        start_index=start_index,
        candidate_count=1,
        run_id="lock-isolation",
        max_per_run=1,
    )
    lock = sqlite3.connect(database, isolation_level=None)
    lock.execute("BEGIN EXCLUSIVE")
    started = time.perf_counter()
    try:
        status = runtime.flush()
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        lock.rollback()
        lock.close()
    return {
        "status": status.status,
        "warning_codes": list(status.warning_codes),
        "elapsed_ms": round(elapsed_ms, 3),
        "configured_timeout_ms": DEFAULT_SQLITE_TIMEOUT_MS,
        "failure_isolated": status.status == "WARN" and "database_write_failed" in status.warning_codes,
    }


def _check_batch_limit(database: Path, start_index: int) -> dict[str, Any]:
    runtime = _runtime(database, run_id="batch-limit", max_per_run=5)
    accepted = 0
    for index in range(start_index, start_index + 6):
        rejected = index % 2 == 0
        accepted += int(
            runtime.record_evaluation(
                market=_market(index),
                observed_price=0.50,
                ai_prior=0.70,
                fallback_prior_used=False,
                raw_edge=40.0,
                fee_adjusted_edge=35.0,
                required_threshold=3.0,
                rejection_reason="edge_below_threshold" if rejected else None,
                gate_failure_kinds=["edge_below_threshold"] if rejected else [],
                order_intent_created=not rejected,
                intent_price=0.49 if not rejected else None,
                maker_assumption="maker" if not rejected else "unknown",
                expected_value=35.0,
            )
        )
    status = runtime.flush()
    return {
        "accepted_candidates": accepted,
        "dropped_count": status.dropped_count,
        "warning_codes": list(status.warning_codes),
        "enforced": accepted == 5 and status.dropped_count == 1 and "capture_limit_reached" in status.warning_codes,
    }


def _classify(
    scale_results: Sequence[dict[str, Any]],
    lock: dict[str, Any],
) -> tuple[str, str, str, list[str]]:
    reasons: list[str] = []
    invariants = all(
        result["append_only"]["update_rejected"]
        and result["append_only"]["delete_rejected"]
        and result["idempotency"]["preserved"]
        and result["batch_limit"]["enforced"]
        and result["migration_latency"]["valid"]
        and result["migration_latency"]["history_preserved"]
        for result in scale_results
    ) and bool(lock["failure_isolated"])
    if not invariants:
        return (
            "FAIL_UNBOUNDED",
            "FAIL_UNBOUNDED",
            "FAIL_UNBOUNDED",
            ["one or more safety invariants failed"],
        )

    timeout_ms = DEFAULT_SQLITE_TIMEOUT_MS
    max_append_p99 = max(result["append_flush_latency"]["p99_ms"] for result in scale_results)
    baseline = max(scale_results[0]["append_flush_latency"]["p50_ms"], 0.001)
    growth_ratio = max(result["append_flush_latency"]["p50_ms"] for result in scale_results) / baseline
    populated = [
        result
        for result in scale_results
        if result["history_scale_candidates"] >= 1_000
    ]
    size_values = [result["database"]["bytes_per_event"] for result in populated]
    size_ratio = max(size_values) / max(min(size_values), 0.001) if size_values else 1.0

    if max_append_p99 >= timeout_ms:
        reasons.append("append flush p99 reached or exceeded the configured SQLite timeout")
        capture_classification = "FAIL_UNBOUNDED"
    elif growth_ratio > 3.0:
        reasons.append("append p50 grew by more than 3x across accumulated scales")
        capture_classification = "FAIL_UNBOUNDED"
    elif max_append_p99 >= timeout_ms * 0.80 or growth_ratio > 2.0:
        reasons.append("append path remained bounded but has less than 20 percent timeout headroom or elevated growth")
        capture_classification = "WARN_LOW_HEADROOM"
    else:
        capture_classification = "PASS_BOUNDED"

    target = max(scale_results, key=lambda result: result["history_scale_candidates"])
    status_p95 = target["status_cli_latency"]["p95_ms"]
    quick_validation_p95 = target["quick_validation_latency"]["p95_ms"]
    if status_p95 >= 5_000 or quick_validation_p95 >= 10_000:
        if status_p95 >= 5_000:
            reasons.append("quick status p95 did not meet the five-second target")
        if quick_validation_p95 >= 10_000:
            reasons.append("quick validation p95 did not meet the ten-second target")
        read_classification = "WARN_NEEDS_OPTIMIZATION_BEFORE_ACTIVATION"
    else:
        read_classification = "PASS_BOUNDED"

    if size_ratio > 2.5:
        reasons.append("bytes per event varied pathologically across scales")
        capture_classification = "FAIL_UNBOUNDED"

    if "FAIL_UNBOUNDED" in {capture_classification, read_classification}:
        overall = "FAIL_UNBOUNDED"
    elif capture_classification != "PASS_BOUNDED" or read_classification != "PASS_BOUNDED":
        overall = "WARN_NEEDS_OPTIMIZATION_BEFORE_ACTIVATION"
    else:
        overall = "PASS_BOUNDED"
    if not reasons:
        reasons = [
            "bounded append retained timeout headroom",
            "quick status and validation met accumulated-ledger targets",
            "deep validation completed exhaustively as an offline operation",
        ]
    return capture_classification, read_classification, overall, reasons


def run_benchmark(
    work_directory: Path,
    *,
    scales: Iterable[int] = DEFAULT_SCALES,
    repeats: int = DEFAULT_REPEATS,
    append_candidates: int = DEFAULT_CAPTURE_MAX_PER_RUN,
) -> dict[str, Any]:
    root = validate_benchmark_root(work_directory)
    selected_scales = tuple(int(scale) for scale in scales)
    if not selected_scales or selected_scales[0] != 0 or any(scale < 0 for scale in selected_scales):
        raise ValueError("scales must start at zero and be non-negative")
    if sorted(set(selected_scales)) != list(selected_scales):
        raise ValueError("scales must be strictly increasing")
    if not 2 <= repeats <= 20:
        raise ValueError("repeats must be from 2 to 20")
    if not 1 <= append_candidates <= DEFAULT_CAPTURE_MAX_PER_RUN:
        raise ValueError("append_candidates must not exceed the implementation default")

    overall_started = time.perf_counter()
    results: list[dict[str, Any]] = []
    global_next_index = 10_000_000
    lock_result: dict[str, Any] | None = None

    for scale in selected_scales:
        database = root / f"scale-{scale}.sqlite3"
        with CandidateLedger.initialize(database, timeout_ms=DEFAULT_SQLITE_TIMEOUT_MS):
            pass
        population = populate_history(database, scale)
        migration_latency = _measure_populated_migration(database)
        with CandidateLedger.open_read_only(database) as ledger:
            before = ledger.summary()
        if int(before["candidate_count"]) != scale:
            raise RuntimeError("population candidate count mismatch")

        end_to_end: list[float] = []
        flush_only: list[float] = []
        last_run_id = ""
        last_start = global_next_index
        written_per_run = 0
        for repeat in range(repeats):
            last_start = global_next_index
            overall_ms, flush_ms, written, last_run_id = _measure_append(
                database,
                start_index=global_next_index,
                repeat=repeat,
                candidate_count=append_candidates,
            )
            end_to_end.append(overall_ms)
            flush_only.append(flush_ms)
            written_per_run = written
            global_next_index += append_candidates

        retry = _prepare_capture_batch(
            database,
            start_index=last_start,
            candidate_count=append_candidates,
            run_id=last_run_id,
        ).flush()
        idempotency = {
            "retry_status": retry.status,
            "retry_written_count": retry.written_count,
            "preserved": retry.status == "PASS" and retry.written_count == 0,
        }
        batch_limit = _check_batch_limit(database, global_next_index)
        global_next_index += 6
        append_only = _check_append_only(database)
        diagnostic_repeats = repeats if scale <= 10_000 else min(repeats, 3)
        status_latency = _measure_status_cli(database, diagnostic_repeats)
        status_latency["repeat_count"] = diagnostic_repeats
        quick_validation_latency = _measure_validation(
            database, diagnostic_repeats, deep=False
        )
        quick_validation_latency["repeat_count"] = diagnostic_repeats
        deep_validation_latency = _measure_validation(
            database, diagnostic_repeats, deep=True
        )
        deep_validation_latency["repeat_count"] = diagnostic_repeats
        database_profile = _database_profile(database)
        if scale == selected_scales[-1]:
            lock_result = _check_lock_isolation(database, global_next_index)
            with CandidateLedger.open_read_only(database) as ledger:
                post_lock_migration = validate_migrations(ledger.connection)
                post_lock_summary = ledger.summary()
            lock_result["post_lock_validation_valid"] = bool(
                post_lock_migration["valid"]
                and int(post_lock_summary["event_count"]) == database_profile["event_count"]
            )

        append_timing = timing_summary(end_to_end)
        append_timing["candidate_count_per_run"] = append_candidates
        append_timing["event_count_per_run"] = written_per_run
        append_timing["average_ms_per_candidate"] = round(append_timing["mean_ms"] / append_candidates, 6)
        append_timing["average_ms_per_event"] = round(append_timing["mean_ms"] / written_per_run, 6)
        flush_timing = timing_summary(flush_only)
        flush_timing["configured_sqlite_timeout_ms"] = DEFAULT_SQLITE_TIMEOUT_MS
        results.append(
            {
                "history_scale_candidates": scale,
                "history_population": population,
                "append_end_to_end_latency": append_timing,
                "append_flush_latency": flush_timing,
                "status_cli_latency": status_latency,
                "quick_validation_latency": quick_validation_latency,
                "deep_validation_latency": deep_validation_latency,
                "migration_latency": migration_latency,
                "idempotency": idempotency,
                "batch_limit": batch_limit,
                "append_only": append_only,
                "database": database_profile,
                "transaction_count": 2 + population["transaction_count"] + repeats + 1 + 1,
            }
        )

    assert lock_result is not None
    capture_classification, read_classification, classification, reasons = _classify(
        results, lock_result
    )
    total_wall_ms = (time.perf_counter() - overall_started) * 1000.0
    index_growth = [
        {
            "history_scale_candidates": result["history_scale_candidates"],
            "database_bytes": result["database"]["database_bytes"],
            "event_count": result["database"]["event_count"],
            "bytes_per_event": result["database"]["bytes_per_event"],
            "total_index_bytes": result["database"]["total_index_bytes"],
        }
        for result in results
    ]
    return {
        "benchmark_version": "candidate-ledger-accumulated-v2",
        "synthetic_local_only": True,
        "production_latency_claimed": False,
        "network_used": False,
        "production_path_used": False,
        "raw_candidate_values_included": False,
        "quick_status_contract": "bounded counts, structural integrity, schema metadata, and one latest candidate payload",
        "quick_validation_contract": "structural integrity plus at most 100 recent payload and relationship checks",
        "deep_validation_contract": "exhaustive offline historical payload, hash, schema, and relationship validation",
        "implementation_defaults": {
            "capture_max_per_run": DEFAULT_CAPTURE_MAX_PER_RUN,
            "sqlite_timeout_ms": DEFAULT_SQLITE_TIMEOUT_MS,
        },
        "event_mix": {
            "candidate_observed": "100% of synthetic candidates",
            "gate_evaluated": "100% of synthetic candidates",
            "rejected_candidates": "50% of synthetic history candidates",
            "order_intent_created": "50% of synthetic history candidates",
            "order_attempted": "50% of synthetic history candidates",
            "order_result": "50% of synthetic history candidates",
        },
        "repeats": repeats,
        "scales": list(selected_scales),
        "results": results,
        "lock_timeout": lock_result,
        "index_growth": index_growth,
        "index_growth_pathological": classification == "FAIL_UNBOUNDED",
        "capture_write_path_classification": capture_classification,
        "read_path_classification": read_classification,
        "classification": classification,
        "classification_reasons": reasons,
        "total_wall_ms": round(total_wall_ms, 3),
    }
