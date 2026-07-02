# Strategy Consolidation Notes — 2026-07-02

Phase: `strategy_consolidation_fable5_safe` (post pre-Fable safety closeout PASS).
Scope: dead-file audit of five candidate strategy files plus behavior-preserving
cleanup of `strategies/kalshi_optimize.py`. No push, no cron/live/Discord changes.

## Dead-candidate audit results

| Candidate | Decision | Evidence |
|---|---|---|
| `strategies/kalshi_optimize_fixed.py` | MISSING_ALREADY | Absent before this phase; no file to delete. |
| `strategies/kalshi_optimize_fixed2.py` | MISSING_ALREADY | Absent before this phase. |
| `strategies/kalshi_optimize_fixed3.py` | MISSING_ALREADY | Absent before this phase. |
| `strategies/sef_spot_trading.py` | KEEP_LIVE | Imported by `runner.py:36` and run as phase 2 (`run_phase2_sef_spot_trading`, `runner.py:189`); also invoked by `scripts/shadow_test_runner.sh:40`. |
| `strategies/stock_hunter.py` | KEEP_LIVE | Imported by `runner.py:37` and run as phase 3 (`run_phase3_stock_hunter`, `runner.py:211`); imported by `diagnostic.py:534,541`; proof files consumed by `strategies/signal_aggregator.py` and `scripts/autoresearch_scorer.py`; invoked by `scripts/shadow_test_runner.sh:44`. |

**Files deleted: none.** The three `kalshi_optimize_fixed*` variants were already
gone, and the other two candidates are live runner phases, not dead code. Full
grep evidence is in the proof dir
(`dead_candidate_reference_audit.txt`, `delete_decisions.md`).

## kalshi_optimize.py cleanup (behavior-preserving only)

- Removed unused module imports `json` and `pathlib.Path` (zero usages).
- Removed stale `# Import runner module` comment (no runner import exists there).
- Removed stale `# Stub for missing function` comment above
  `check_micro_live_gates` — the function is a full implementation, not a stub.
- Removed duplicate local `from datetime import datetime` inside
  `check_micro_live_gates` (same class already imported at module level).
- Removed duplicate local `from utils.proof import generate_proof` near the end
  of `optimize_kalshi_strategy` (already imported unconditionally at module level).

No changes to order placement, Kelly sizing, edge thresholds, calibration math,
vol gate, price-floor gates, weather logic, Discord send behavior, pnl.db
writes, or runner phase-all behavior.

## Observed anomaly — deliberately NOT changed

In the dry-run branch of `optimize_kalshi_strategy`, the Discord
`notify_order_placed` block is indented inside the `except Exception:` handler
of the dry-run `record_trade` call, so it only executes if `record_trade`
raises. This looks unintentional, but "fixing" it would change Discord send
behavior (dry-run runs would start emitting notifications), which is forbidden
in this phase. Left byte-for-byte as found; flagged for a separate reviewed
change if the operator wants dry-run notifications.
