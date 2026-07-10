from scripts import main_run_gate_summary_redacted as summary


def test_latest_run_classifies_no_profitable_maker_market_without_order_intents():
    lines = [
        "2026-07-09 08:00:20,149 | INFO | Fetching Kalshi markets...",
        "2026-07-09 08:00:30,381 | INFO | Fetched 215 markets",
        "2026-07-09 08:00:30,401 | INFO | [EXPIRY] Filtered 215 -> 101 markets (max_days=3)",
        "2026-07-09 08:00:30,401 | INFO | [CATEGORY] Filtered 101 -> 101 markets (allowed=commodities,crypto,economics,financials,index,politics)",
        "2026-07-09 08:00:36,803 | INFO | [kelly] AI prior: source=gemini prob=0.650 (premium market: KXNASDAQ100U-26JUL09H1600-T29319.99)",
        "2026-07-09 08:01:33,204 | WARNING | No profitable maker markets found",
        "2026-07-09 08:01:33,220 | INFO | Total orders placed: 0",
        "2026-07-09 08:01:36,753 | INFO |   order_submission: elapsed=0.0s exhausted=False processed=0 skipped=0",
        "2026-07-09 08:01:36,753 | INFO | Exit code: 0",
    ]

    result = summary.summarize_lines(lines)

    assert result.status == "PASS"
    assert result.markets_fetched == 215
    assert result.after_expiry == 101
    assert result.after_category == 101
    assert result.ai_processed == 1
    assert result.order_intents == 0
    assert result.edge_or_profitability_blocked == 1
    assert result.zero_order_reason == "edge_or_profitability_blocked"


def test_latest_run_classifies_price_gate_when_all_intents_are_price_skips():
    lines = [
        "2026-07-08 18:00:20,149 | INFO | Fetching Kalshi markets...",
        "2026-07-08 18:00:29,328 | INFO | Fetched 238 markets",
        "2026-07-08 18:00:29,353 | INFO | [EXPIRY] Filtered 238 -> 90 markets (max_days=3)",
        "2026-07-08 18:00:29,354 | INFO | [CATEGORY] Filtered 90 -> 90 markets (allowed=commodities,crypto,economics,financials,index)",
        "2026-07-08 18:01:29,526 | INFO | [kelly] AI prior: source=gemini prob=0.650 (premium market: KXNASDAQ100U-26JUL08H1600-T29799.99)",
        "2026-07-08 18:01:29,539 | INFO | Market KXNASDAQ100U-26JUL08H1600-T29799.99: YES order (limit) at 0.0792 (will pay taker fee on fill)",
        "2026-07-08 18:01:29,539 | INFO | [PRICE] Skipping KXNASDAQ100U-26JUL08H1600-T29799.99: price 8c < min 25c",
        "2026-07-08 18:01:29,539 | INFO | Total orders placed: 0",
        "2026-07-08 18:01:29,559 | INFO |   order_submission: elapsed=0.0s exhausted=False processed=0 skipped=0",
        "2026-07-08 18:01:29,559 | INFO | Exit code: 0",
    ]

    result = summary.summarize_lines(lines)

    assert result.order_intents == 1
    assert result.price_gate_blocked == 1
    assert result.zero_order_reason == "price_gate_blocked"


def test_summary_filters_secret_marker_lines_from_counts():
    lines = [
        "2026-07-09 08:00:20,149 | INFO | Fetching Kalshi markets...",
        "2026-07-09 08:00:30,381 | INFO | Fetched 215 markets",
        "2026-07-09 08:00:30,401 | INFO | [EXPIRY] Filtered 215 -> 101 markets (max_days=3)",
        "2026-07-09 08:00:30,401 | INFO | [CATEGORY] Filtered 101 -> 101 markets (allowed=index)",
        "2026-07-09 08:00:30,402 | INFO | authorization bearer fake should be ignored [PRICE] Skipping KX: price 1c < min 25c",
        "2026-07-09 08:01:33,220 | INFO | Total orders placed: 0",
    ]

    result = summary.summarize_lines(lines)
    output = summary.format_summary(result)

    assert result.price_gate_blocked == 0
    assert "fake" not in output
    assert "VALUES_PRINTED=no_secret_values" in output


def test_latest_run_reads_redacted_post_intent_diagnostics():
    lines = [
        "2026-07-10 12:00:20,149 | INFO | Fetching Kalshi markets...",
        "2026-07-10 12:00:30,381 | INFO | Fetched 10 markets",
        "2026-07-10 12:00:30,401 | INFO | [kelly] AI prior: source=gemini prob=0.650 (premium market: KXTEST)",
        "2026-07-10 12:00:31,539 | INFO | Market KXTEST: YES order (limit) at 0.5000 (will pay taker fee on fill)",
        "2026-07-10 12:00:31,559 | INFO | [ORDER_DIAG] POST_INTENT_BLOCKER_COUNTS=duplicate_or_open_position:1",
        "2026-07-10 12:00:31,559 | INFO | [ORDER_DIAG] ORDER_INTENT_TO_SUBMISSION_STATUS=intents:1,attempted:0,succeeded:0,failed:0",
        "2026-07-10 12:00:31,559 | INFO | [ORDER_DIAG] SUBMISSION_SKIPPED_REASON_COUNTS=duplicate_or_open_position:1",
    ]

    result = summary.summarize_lines(lines)
    output = summary.format_summary(result, artifact=summary.DEFAULT_LOG)

    assert result.post_intent_blocker_counts == "duplicate_or_open_position:1"
    assert result.order_intent_to_submission_status == "intents:1,attempted:0,succeeded:0,failed:0"
    assert result.submission_skipped_reason_counts == "duplicate_or_open_position:1"
    assert "LATEST_RUN_ARTIFACT=logs/cron_micro_live.log" in output
