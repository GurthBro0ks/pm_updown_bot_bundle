#!/usr/bin/env python3
"""
Daily Funnel Report — Reliability Phase 1, Module 3

Produces a one-page markdown report summarizing the last N hours of bot
activity.  Reads from existing data sources (scratchpad JSONL, pnl.db,
cron logs, cursor/breaker state files).  Never calls external APIs.

Usage:
    python3 scripts/daily_funnel_report.py --dry-run
    python3 scripts/daily_funnel_report.py --hours 12
    python3 scripts/daily_funnel_report.py --discord
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as global_config


def _parse_ts(ts_str):
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            continue
    return None


def _load_jsonl(path, window_start):
    entries = []
    if not Path(path).exists():
        return entries
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_key = "ts" if "ts" in entry else "timestamp"
            ts_str = entry.get(ts_key, "")
            ts = _parse_ts(ts_str)
            if ts and ts >= window_start:
                entry["_ts"] = ts
                entries.append(entry)
    return entries


def _git_info(repo_path):
    info = {"branch": "unknown", "commit_hash": "unknown", "commit_msg": ""}
    try:
        info["branch"] = subprocess.check_output(
            ["git", "branch", "--show-current"],
            cwd=repo_path, stderr=subprocess.DEVNULL
        ).decode().strip()
        info["commit_hash"] = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_path, stderr=subprocess.DEVNULL
        ).decode().strip()
        info["commit_msg"] = subprocess.check_output(
            ["git", "log", "-1", "--format=%s"],
            cwd=repo_path, stderr=subprocess.DEVNULL
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return info


def _fmt_ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _section_header(title):
    return f"\n## {title}\n"


def build_section_header(window_hours, window_start, window_end, repo_path):
    git = _git_info(repo_path)
    now = datetime.now(timezone.utc)
    lines = [
        f"# SlimyAI Daily Funnel Report — {now.strftime('%Y-%m-%d')}",
        "",
        f"**Generated:** {_fmt_ts(now)}",
        f"**Window:** last {window_hours}h ({_fmt_ts(window_start)} → {_fmt_ts(window_end)})",
        f"**Branch:** `{git['branch']}`",
        f"**Last commit:** `{git['commit_hash']}` — {git['commit_msg']}",
    ]
    return "\n".join(lines)


def build_section_cron_health(cron_log_path, window_start, window_end):
    lines = [_section_header("Cron Health Summary")]
    if not Path(cron_log_path).exists():
        lines.append("*No cron log found.*")
        return "\n".join(lines)

    run_starts = []
    run_end_times = {}
    orders_placed = defaultdict(int)
    orders_filled = defaultdict(int)
    exceptions = defaultdict(list)
    exit_codes = {}

    ts_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+")
    run_id_pattern = re.compile(r"CRON RUN ID: ([\w-]+)")
    start_pattern = re.compile(r"MODE: (MICRO-?LIVE|SHADOW|REAL-?LIVE)")
    exit_pattern = re.compile(r"Exit code: (\d+)")
    order_placed_pattern = re.compile(r"order.*placed|PLACED.*ORDER|MICRO-LIVE.*ORDER", re.IGNORECASE)
    order_filled_pattern = re.compile(r"order.*filled|FILLED", re.IGNORECASE)
    error_pattern = re.compile(r"\bERROR\b")

    current_run_id = None
    current_run_ts = None

    with open(cron_log_path, "r") as f:
        for raw_line in f:
            m = ts_pattern.match(raw_line)
            if not m:
                continue
            line_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if line_ts < window_start or line_ts > window_end:
                continue

            rid = run_id_pattern.search(raw_line)
            if rid:
                current_run_id = rid.group(1)
                current_run_ts = line_ts
                run_starts.append({"id": current_run_id, "ts": line_ts})
                continue

            if current_run_id:
                ep = exit_pattern.search(raw_line)
                if ep:
                    exit_codes[current_run_id] = int(ep.group(1))
                    run_end_times[current_run_id] = line_ts
                    current_run_id = None
                    current_run_ts = None
                    continue

                if order_placed_pattern.search(raw_line):
                    orders_placed[current_run_id] += 1

                if order_filled_pattern.search(raw_line):
                    orders_filled[current_run_id] += 1

                if error_pattern.search(raw_line):
                    exceptions[current_run_id].append(raw_line.strip()[:200])

    if not run_starts:
        lines.append("*No cron runs in window.*")
        return "\n".join(lines)

    lines.append("| timestamp | run_id | exit | wall_s | orders_placed | orders_filled |")
    lines.append("|-----------|--------|------|--------|---------------|---------------|")

    total_clean = 0
    total_placed = 0
    total_filled = 0
    for run in run_starts:
        rid = run["id"]
        ts_str = run["ts"].strftime("%H:%M:%S")
        exit_code = exit_codes.get(rid, "?")
        end_ts = run_end_times.get(rid)
        wall = f"{(end_ts - run['ts']).total_seconds():.0f}s" if end_ts else "?"
        placed = orders_placed.get(rid, 0)
        filled = orders_filled.get(rid, 0)
        total_placed += placed
        total_filled += filled
        if exit_code == 0:
            total_clean += 1
        short_id = rid[:8]
        lines.append(f"| {ts_str} | {short_id} | {exit_code} | {wall} | {placed} | {filled} |")

    lines.append("")
    lines.append(f"**Totals:** {len(run_starts)} runs, {total_clean} clean exits, "
                 f"{total_placed} orders placed, {total_filled} orders filled")
    if exceptions:
        lines.append(f"**Errors:** {sum(len(v) for v in exceptions.values())} error lines across "
                     f"{len(exceptions)} runs")
    return "\n".join(lines)


def build_section_stage_budget(scratchpad_dir, window_start, stage_budgets_config):
    lines = [_section_header("Stage Budget Utilization")]
    events = _load_jsonl(Path(scratchpad_dir) / "stage_budget.jsonl", window_start)

    stage_names = list(stage_budgets_config.keys())
    budget_data = {s: {"elapsed": [], "exhausted_count": 0, "budget": stage_budgets_config[s]}
                   for s in stage_names}

    for ev in events:
        stage = ev.get("stage_name", "")
        if stage not in budget_data:
            continue
        elapsed = ev.get("elapsed_seconds", 0)
        if ev.get("exhausted"):
            budget_data[stage]["exhausted_count"] += 1
        budget_data[stage]["elapsed"].append(elapsed)

    if not any(v["elapsed"] for v in budget_data.values()):
        lines.append("*No stage budget events in window.*")
        return "\n".join(lines)

    lines.append("| stage | avg_elapsed | max_elapsed | budget | exhausted_count | flag |")
    lines.append("|-------|-------------|-------------|--------|-----------------|------|")

    for stage in stage_names:
        d = budget_data[stage]
        if not d["elapsed"]:
            lines.append(f"| {stage} | — | — | {d['budget']}s | 0 | |")
            continue
        avg_e = sum(d["elapsed"]) / len(d["elapsed"])
        max_e = max(d["elapsed"])
        budget = d["budget"]
        exc = d["exhausted_count"]
        total_runs = len(d["elapsed"])
        flag = ""
        if total_runs > 0 and exc / total_runs > 0.2:
            flag = "⚠️ SUSPECT"
        if budget > 0 and avg_e / budget > 0.8:
            flag += " 🔶 TIGHT"
        lines.append(f"| {stage} | {avg_e:.1f}s | {max_e:.1f}s | {budget}s | {exc} | {flag} |")
    return "\n".join(lines)


def build_section_cascade_cursor(scratchpad_dir, cursor_path, window_start):
    lines = [_section_header("Cascade + Rotation State")]

    cursor_state_events = _load_jsonl(Path(scratchpad_dir) / "cursor_state.jsonl", window_start)
    cursor_advance_events = _load_jsonl(Path(scratchpad_dir) / "cursor_advance.jsonl", window_start)
    cursor_reset_events = _load_jsonl(Path(scratchpad_dir) / "cursor_reset.jsonl", window_start)
    cascade_events = _load_jsonl(Path(scratchpad_dir) / "cascade_budget_exhausted.jsonl", window_start)

    cursor_data = {}
    if Path(cursor_path).exists():
        try:
            with open(cursor_path, "r") as f:
                cursor_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    current_index = cursor_data.get("index", "?")
    list_hash = cursor_data.get("list_hash", "?")[:12] if cursor_data.get("list_hash") else "?"
    total_rotations = cursor_data.get("total_rotations", 0)

    if cursor_advance_events:
        last_list_size = cursor_advance_events[-1].get("total_markets", "?")
    elif cursor_state_events:
        last_list_size = cursor_state_events[-1].get("list_size", "?")
    else:
        last_list_size = "?"

    avg_markets = 0
    if cursor_advance_events:
        avg_markets = sum(e.get("markets_processed", 0) for e in cursor_advance_events) / len(cursor_advance_events)

    lines.append(f"- **Current cursor:** index={current_index}, list_size={last_list_size}, "
                 f"total_rotations={total_rotations}")
    lines.append(f"- **Avg markets processed/run:** {avg_markets:.1f}")

    if isinstance(last_list_size, (int, float)) and avg_markets > 0 and last_list_size > 0:
        runs_per_day = len(cursor_advance_events)
        if runs_per_day > 0:
            markets_per_day = avg_markets * runs_per_day
            days_to_rotate = last_list_size / markets_per_day if markets_per_day > 0 else float("inf")
            lines.append(f"- **Estimated days to full rotation:** {days_to_rotate:.1f}")
    else:
        lines.append("- **Estimated days to full rotation:** unknown (no data)")

    if cursor_reset_events:
        lines.append(f"- ⚠️ **Cursor reset** occurred {len(cursor_reset_events)} time(s) in window")
        for ev in cursor_reset_events[:3]:
            ts = ev.get("_ts", "")
            reason = ev.get("reason", "hash changed")
            lines.append(f"  - {_fmt_ts(ts) if isinstance(ts, datetime) else ts}: {reason}")

    if cascade_events:
        lines.append(f"- **Cascade budget exhausted:** {len(cascade_events)} event(s)")
        for ev in cascade_events[:3]:
            priors = ev.get("markets_with_priors", "?")
            total = ev.get("markets_total", "?")
            lines.append(f"  - {priors}/{total} markets got priors before budget exhaustion")

    return "\n".join(lines)


def build_section_provider_health(breaker_state_path, scratchpad_dir, window_start):
    lines = [_section_header("Provider Health")]

    breaker_data = {}
    if Path(breaker_state_path).exists():
        try:
            with open(breaker_state_path, "r") as f:
                breaker_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    cascade_down_events = _load_jsonl(Path(scratchpad_dir) / "cascade_budget_exhausted.jsonl", window_start)

    if not breaker_data:
        lines.append("*No breaker state file found.*")
        return "\n".join(lines)

    lines.append("| provider | calls | successes | failures | rate | state | flag |")
    lines.append("|----------|-------|-----------|----------|------|-------|------|")

    for name, data in sorted(breaker_data.items()):
        total_calls = data.get("total_calls", 0)
        total_failures = data.get("total_failures", 0)
        successes = total_calls - total_failures
        rate = f"{successes / total_calls:.0%}" if total_calls > 0 else "—"
        state = data.get("state", "UNKNOWN")
        flag = ""
        if state in ("OPEN", "HALF_OPEN"):
            flag = f"⚠️ {state}"
        lines.append(f"| {name} | {total_calls} | {successes} | {total_failures} | {rate} | {state} | {flag} |")

    all_down_count = 0
    for ev in _load_jsonl(Path(scratchpad_dir) / "prior_validation.jsonl", window_start):
        pass

    if not any(d.get("state") not in ("CLOSED",) for d in breaker_data.values()):
        lines.append("\n**All providers healthy (CLOSED).**")
    else:
        open_providers = [n for n, d in breaker_data.items() if d.get("state") in ("OPEN", "HALF_OPEN")]
        lines.append(f"\n⚠️ **Non-CLOSED providers:** {', '.join(open_providers)}")

    return "\n".join(lines)


def build_section_trading(pnl_db_path, window_start, window_end):
    lines = [_section_header("Trading Activity")]

    if not Path(pnl_db_path).exists():
        lines.append("*No pnl.db found.*")
        return "\n".join(lines)

    try:
        conn = sqlite3.connect(f"file:{pnl_db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        lines.append("*Could not open pnl.db.*")
        return "\n".join(lines)

    try:
        ws = window_start.strftime("%Y-%m-%d %H:%M:%S")
        we = window_end.strftime("%Y-%m-%d %H:%M:%S")

        buys = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_usd), 0) FROM trades "
            "WHERE timestamp >= ? AND timestamp <= ? AND action = 'BUY'",
            (ws, we)
        ).fetchone()
        exits = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl_usd), 0) FROM trades "
            "WHERE timestamp >= ? AND timestamp <= ? AND action = 'EXIT'",
            (ws, we)
        ).fetchone()

        total_buys = buys[0]
        total_cost = buys[1]
        total_exits = exits[0]
        total_pnl = exits[1]

        prev_ws = window_start - (window_end - window_start)
        prev_ws_str = prev_ws.strftime("%Y-%m-%d %H:%M:%S")
        prev_buys = conn.execute(
            "SELECT COUNT(*) FROM trades "
            "WHERE timestamp >= ? AND timestamp < ? AND action = 'BUY'",
            (prev_ws_str, ws)
        ).fetchone()[0]

        top_orders = conn.execute(
            "SELECT ticker, action, price, size_usd, timestamp FROM trades "
            "WHERE timestamp >= ? AND timestamp <= ? "
            "ORDER BY size_usd DESC LIMIT 5",
            (ws, we)
        ).fetchall()

        last_equity = conn.execute(
            "SELECT cash, total_value FROM equity_snapshots ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

        open_positions = conn.execute(
            "SELECT ticker, size_usd, price FROM trades "
            "WHERE action = 'BUY' AND ticker NOT IN "
            "(SELECT ticker FROM trades WHERE action = 'EXIT') "
            "GROUP BY ticker HAVING MAX(timestamp)"
        ).fetchall()

    finally:
        conn.close()

    fill_rate = f"{total_exits / total_buys:.0%}" if total_buys > 0 else "—"
    trend_arrow = ""
    if total_buys > 0 and prev_buys > 0:
        prev_fill = min(total_exits / total_buys, 1.0) if total_buys > 0 else 0
        curr_fill = total_exits / total_buys if total_buys > 0 else 0
        if curr_fill > prev_fill + 0.05:
            trend_arrow = " ↗"
        elif curr_fill < prev_fill - 0.05:
            trend_arrow = " ↘"
        else:
            trend_arrow = " →"

    lines.append(f"- **Orders placed:** {total_buys} (total cost: ${total_cost:.2f})")
    lines.append(f"- **Orders filled (exits):** {total_exits} (fill rate: {fill_rate}{trend_arrow})")
    lines.append(f"- **Realized P&L in window:** ${total_pnl:.2f}")

    if open_positions:
        total_exposure = sum(r[1] for r in open_positions)
        lines.append(f"- **Open positions:** {len(open_positions)} (total exposure: ${total_exposure:.2f})")
    else:
        lines.append("- **Open positions:** 0")

    if last_equity:
        lines.append(f"- **Cash remaining:** ${last_equity[0]:.2f}")
        lines.append(f"- **Total portfolio value:** ${last_equity[1]:.2f}")

    if top_orders:
        lines.append("")
        lines.append("**Top 5 orders by size:**")
        lines.append("| ticker | action | price | size_usd | timestamp |")
        lines.append("|--------|--------|-------|----------|-----------|")
        for row in top_orders:
            lines.append(f"| {row[0][:30]} | {row[1]} | {row[2]:.2f} | ${row[3]:.2f} | {row[4]} |")
    else:
        lines.append("*No trades in window.*")

    return "\n".join(lines)


def build_section_anomalies(scratchpad_dir, cron_log_path, pnl_db_path,
                            window_start, window_end, stage_budgets_config):
    lines = [_section_header("Anomalies and Flags")]
    anomalies = []

    canary_fail = _load_jsonl(Path(scratchpad_dir) / "canary_failure.jsonl", window_start)
    for ev in canary_fail:
        ts = ev.get("_ts", "")
        check = ev.get("failed_check_name", "unknown")
        err = ev.get("error_message", "")[:80]
        ts_fmt = _fmt_ts(ts) if isinstance(ts, datetime) else str(ts)
        anomalies.append(f"{ts_fmt} — **CANARY FAILURE**: {check}: {err}")

    cascade_exhaust = _load_jsonl(Path(scratchpad_dir) / "cascade_budget_exhausted.jsonl", window_start)
    for ev in cascade_exhaust:
        ts = ev.get("_ts", "")
        priors = ev.get("markets_with_priors", "?")
        total = ev.get("markets_total", "?")
        ts_fmt = _fmt_ts(ts) if isinstance(ts, datetime) else str(ts)
        anomalies.append(f"{ts_fmt} — **BUDGET EXHAUSTION**: ai_cascade {priors}/{total} markets")

    cursor_resets = _load_jsonl(Path(scratchpad_dir) / "cursor_reset.jsonl", window_start)
    for ev in cursor_resets:
        ts = ev.get("_ts", "")
        ts_fmt = _fmt_ts(ts) if isinstance(ts, datetime) else str(ts)
        anomalies.append(f"{ts_fmt} — **CURSOR RESET**: hash changed")

    breaker_state_path = global_config.CIRCUIT_BREAKER_PATH
    if Path(breaker_state_path).exists():
        try:
            with open(breaker_state_path, "r") as f:
                breaker_data = json.load(f)
            for name, data in breaker_data.items():
                if data.get("state") in ("OPEN", "HALF_OPEN"):
                    anomalies.append(f"**BREAKER OPEN**: {name} is {data['state']} "
                                     f"(total_opens={data.get('total_opens', 0)})")
        except (json.JSONDecodeError, OSError):
            pass

    if Path(cron_log_path).exists():
        ts_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+")
        traceback_pattern = re.compile(r"Traceback|Exception|Error:.*")
        with open(cron_log_path, "r") as f:
            for raw_line in f:
                m = ts_pattern.match(raw_line)
                if not m:
                    continue
                line_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if line_ts < window_start or line_ts > window_end:
                    continue
                if "Traceback" in raw_line:
                    anomalies.append(f"{_fmt_ts(line_ts)} — **TRACEBACK** in cron log")
                elif re.search(r"\bERROR\b", raw_line) and "ORDER FAILED" in raw_line:
                    short = raw_line.strip()[:120]
                    anomalies.append(f"{_fmt_ts(line_ts)} — **ORDER FAILED**: {short}")

    if Path(pnl_db_path).exists():
        try:
            conn = sqlite3.connect(f"file:{pnl_db_path}?mode=ro", uri=True)
            ws = window_start.strftime("%Y-%m-%d %H:%M:%S")
            we = window_end.strftime("%Y-%m-%d %H:%M:%S")

            buys = conn.execute(
                "SELECT COALESCE(SUM(size_usd), 0) FROM trades "
                "WHERE timestamp >= ? AND timestamp <= ? AND action = 'BUY'",
                (ws, we)
            ).fetchone()[0]

            equity_rows = conn.execute(
                "SELECT cash, total_value, timestamp FROM equity_snapshots "
                "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
                (ws, we)
            ).fetchall()

            if len(equity_rows) >= 2:
                first = equity_rows[0]
                last = equity_rows[-1]
                cash_delta = last[0] - first[0]
                if abs(cash_delta - buys) > 10 and buys > 0:
                    anomalies.append(
                        f"**UNEXPLAINED CASH**: delta=${cash_delta:.2f} vs "
                        f"orders=${buys:.2f} (gap=${abs(cash_delta - buys):.2f})"
                    )
            conn.close()
        except sqlite3.Error:
            pass

    if not anomalies:
        lines.append("No anomalies detected.")
    else:
        for a in anomalies:
            lines.append(f"- {a}")

    return "\n".join(lines)


def build_discord_summary(report_text, report_path):
    lines = report_text.split("\n")
    date_line = next((l for l in lines if l.startswith("# SlimyAI")), "SlimyAI Daily Report")

    cron_section_start = None
    for i, l in enumerate(lines):
        if "Cron Health Summary" in l:
            cron_section_start = i
            break

    runs_line = "Runs: ?"
    if cron_section_start:
        for l in lines[cron_section_start: cron_section_start + 20]:
            if "**Totals:**" in l:
                runs_line = l.replace("**Totals:**", "Runs:").replace("**", "")
                break

    provider_line = "Providers: all healthy"
    breaker_path = global_config.CIRCUIT_BREAKER_PATH
    if Path(breaker_path).exists():
        try:
            with open(breaker_path, "r") as f:
                bd = json.load(f)
            non_closed = [n for n, d in bd.items() if d.get("state") != "CLOSED"]
            if non_closed:
                provider_line = f"Providers: ⚠️ {', '.join(non_closed)} NOT CLOSED"
        except (json.JSONDecodeError, OSError):
            pass

    anomaly_count = report_text.count("- **")
    anomaly_line = f"Anomalies: {anomaly_count}" if anomaly_count > 0 else "Anomalies: 0 — none"

    msg = (
        f"**{date_line.lstrip('# ').strip()}**\n"
        f"{runs_line}\n"
        f"{provider_line}\n"
        f"{anomaly_line}\n"
        f"Full report: `{report_path}`"
    )
    return msg


def post_discord(webhook_url, message):
    if not webhook_url:
        return False
    payload = json.dumps({"content": message}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, Exception):
        return False


def generate_report(window_hours, repo_path, cron_log_path, scratchpad_dir,
                    cursor_path, breaker_path, pnl_db_path, stage_budgets_config):
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=window_hours)
    window_end = now

    sections = [
        build_section_header(window_hours, window_start, window_end, repo_path),
        build_section_cron_health(cron_log_path, window_start, window_end),
        build_section_stage_budget(scratchpad_dir, window_start, stage_budgets_config),
        build_section_cascade_cursor(scratchpad_dir, cursor_path, window_start),
        build_section_provider_health(breaker_path, scratchpad_dir, window_start),
        build_section_trading(pnl_db_path, window_start, window_end),
        build_section_anomalies(scratchpad_dir, cron_log_path, pnl_db_path,
                                window_start, window_end, stage_budgets_config),
    ]
    return "\n".join(sections)


def main():
    parser = argparse.ArgumentParser(description="SlimyAI Daily Funnel Report")
    parser.add_argument("--hours", type=int, default=None,
                        help="Window in hours (default: from config or 24)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output file path (default: runtime/reports/funnel_YYYY-MM-DD.md)")
    parser.add_argument("--discord", action="store_true",
                        help="Post summary to Discord webhook")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print to stdout only, do not write file")
    args = parser.parse_args()

    window_hours = args.hours or global_config.DAILY_REPORT_DEFAULT_WINDOW_HOURS
    repo_path = global_config.BASE_DIR
    cron_log_path = str(global_config.LOGS_DIR / "cron_micro_live.log")
    scratchpad_dir = str(global_config.LOGS_DIR / "scratchpad")
    cursor_path = str(global_config.MARKET_CURSOR_PATH)
    breaker_path = str(global_config.CIRCUIT_BREAKER_PATH)
    pnl_db_path = str(global_config.PAPER_TRADING_DIR / "pnl.db")
    stage_budgets_config = global_config.STAGE_BUDGETS

    report = generate_report(
        window_hours=window_hours,
        repo_path=repo_path,
        cron_log_path=cron_log_path,
        scratchpad_dir=scratchpad_dir,
        cursor_path=cursor_path,
        breaker_path=breaker_path,
        pnl_db_path=pnl_db_path,
        stage_budgets_config=stage_budgets_config,
    )

    if args.dry_run:
        print(report)
        print("\n--- (dry-run: no file written) ---")
        return

    if args.output:
        output_path = Path(args.output)
    else:
        report_dir = global_config.DAILY_REPORT_PATH
        report_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        output_path = report_dir / f"funnel_{date_str}.md"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(f".tmp.{os.getpid()}")
    with open(tmp_path, "w") as f:
        f.write(report)
        f.flush()
        os.fsync(f.fileno())
    tmp_path.rename(output_path)
    print(f"Report written to: {output_path}")

    if args.discord:
        webhook_url = global_config.DISCORD_WEBHOOK_URL
        if webhook_url:
            msg = build_discord_summary(report, str(output_path))
            ok = post_discord(webhook_url, msg)
            if ok:
                print("Discord summary posted.")
            else:
                print("WARNING: Discord POST failed (report file is still the source of truth).")
        else:
            print("Discord webhook not configured (DISCORD_WEBHOOK_URL empty). Skipping.")

    print("\nFOLLOW-UP: add this line to crontab manually:")
    print(f"0 7 * * * {sys.executable} {Path(__file__).resolve()} --discord "
          f">> /var/log/slimyai/daily_report.log 2>&1")


if __name__ == "__main__":
    main()
