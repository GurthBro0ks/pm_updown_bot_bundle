"""
Lightweight Flask API for the trading dashboard.
Reads paper_balance.json and trade_log.json, serves JSON.
Runs on port 851 with Streamlit on0 (avoid conflict 8501).
"""

from flask import Flask, jsonify, request
import json
import os
import logging
from datetime import datetime

app = Flask(__name__)
BASE = "/opt/slimy/pm_updown_bot_bundle"

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def read_json(path):
    """Read JSON file, return empty dict on failure."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read {path}: {e}")
        return {}


@app.route("/api/trading/status")
def trading_status():
    """Main dashboard endpoint — all data in one call."""
    balance = read_json(f"{BASE}/paper_trading/paper_balance.json")
    trade_log = read_json(f"{BASE}/paper_trading/trade_log.json")

    # Read positions from balance file
    positions = balance.get("positions", [])
    cash = balance.get("cash", 0)
    positions_value = sum(p.get("size", 0) for p in positions)

    # Phase status - use config.py PHASES
    try:
        import sys
        sys.path.insert(0, BASE)
        from config import PHASES
        phases = {
            "1_kalshi": PHASES.get("phase1_kalshi", False),
            "2_crypto": PHASES.get("phase2_sef", False),
            "3_stocks": PHASES.get("phase3_stock_hunter", False),
            "4_airdrop": PHASES.get("phase4_airdrop", False),
        }
    except Exception as e:
        logger.warning(f"Failed to load PHASES: {e}")
        phases = {
            "1_kalshi": False,
            "2_crypto": False,
            "3_stocks": False,
            "4_airdrop": False,
        }

    # Recent trades from log
    recent_trades = trade_log.get("entries", [])[-20:] if trade_log else []

    # Calculate PnL if we have starting balance
    starting_balance = 100.0
    total = cash + positions_value
    pnl = total - starting_balance
    pnl_pct = (pnl / starting_balance) * 100 if starting_balance > 0 else 0

    return jsonify({
        "timestamp": datetime.utcnow().isoformat(),
        "bankroll": {
            "cash": cash,
            "positions_value": positions_value,
            "total": total,
            "pnl": pnl,
            "pnl_pct": round(pnl_pct, 2),
        },
        "positions": positions,
        "phases": phases,
        "recent_trades": recent_trades,
        "mode": "paper",
    })


@app.route("/api/trading/health")
def api_health():
    """API connectivity health check."""
    # Check if key files exist and are fresh
    checks = {}
    for name, path in {
        "paper_balance": f"{BASE}/paper_trading/paper_balance.json",
        "trade_log": f"{BASE}/paper_trading/trade_log.json",
    }.items():
        exists = os.path.exists(path)
        age_hours = None
        if exists:
            mtime = os.path.getmtime(path)
            age_hours = (datetime.utcnow().timestamp() - mtime) / 3600
        checks[name] = {
            "exists": exists,
            "age_hours": round(age_hours, 1) if age_hours is not None else None,
            "fresh": age_hours < 24 if age_hours is not None else False,
        }

    # Overall health
    all_fresh = all(c.get("fresh", False) for c in checks.values()) if checks else False

    return jsonify({
        "status": "healthy" if all_fresh else "degraded",
        "checks": checks,
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route("/api/trading/airdrops")
def airdrop_status():
    """Airdrop farming status."""
    state_file = f"{BASE}/airdrop_state.json"
    state = read_json(state_file)

    # Static airdrop targets (can be made dynamic later)
    targets = [
        {"name": "OpenSea $SEA", "tier": "S", "tge": "Q1 2026", "status": "CONFIRMED", "completed": False},
        {"name": "Polymarket $POLY", "tier": "S", "tge": "Q1-Q2 2026", "status": "CONFIRMED - SNAPSHOT PENDING", "completed": False},
        {"name": "MetaMask $MASK", "tier": "S", "tge": "2026", "status": "CONFIRMED", "completed": False},
        {"name": "Base", "tier": "S", "tge": "Q2-Q4 2026", "status": "EXPLORING - #1 PRIORITY", "completed": False},
        {"name": "LayerZero S2", "tier": "A", "tge": "2026", "status": "SEASON_2_LIVE", "completed": False},
        {"name": "Ethena $ENA", "tier": "A", "tge": "2026", "status": "SEASON_5_LIVE", "completed": False},
        {"name": "Monad $MON", "tier": "F", "tge": "Oct 2025", "status": "COMPLETED - TESTNET EXCLUDED", "completed": True},
        {"name": "Linea $LINEA", "tier": "F", "tge": "Sep 2025", "status": "COMPLETED - CLAIM CLOSED", "completed": True},
    ]

    return jsonify({
        "state": state,
        "targets": targets,
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route("/api/trading/signals")
def trading_signals():
    """Current pending signals and force exits."""
    signals = read_json(f"{BASE}/discord_signals.json")

    return jsonify({
        "pending_actions": signals.get("pending_actions", []),
        "force_exits": signals.get("force_exits", []),
        "config_updates": signals.get("config_updates", []),
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route("/health")
def health():
    """Basic health check for the API itself."""
    return jsonify({"status": "ok", "timestamp": datetime.utcnow().isoformat()})


# =============================================================================
# SIGNAL AGGREGATOR ENDPOINTS
# =============================================================================

@app.route("/api/signals/current")
def signals_current():
    """
    Current state of all signal sources in the Bayesian cascade.
    Cached for 60 seconds server-side.
    """
    try:
        from strategies.signal_aggregator import get_all_signals
        signals = get_all_signals()
        return jsonify(signals)
    except Exception as e:
        logger.error(f"[API] /api/signals/current failed: {e}")
        return jsonify({"error": str(e), "sources": {}}), 500


@app.route("/api/signals/history")
def signals_history():
    """
    Time-series of signal values over the last N hours.
    Query params:
      hours: int (default 24, max 168)
      interval: int (minutes, default 30, min 5, max 120)
    """
    try:
        hours = request.args.get("hours", 24, type=int)
        interval = request.args.get("interval", 30, type=int)
        hours = max(1, min(168, hours))
        interval = max(5, min(120, interval))

        from strategies.signal_aggregator import get_signal_history
        history = get_signal_history(hours=hours, interval_minutes=interval)
        return jsonify({
            "history": history,
            "hours": hours,
            "interval_minutes": interval,
            "timestamp": datetime.utcnow().isoformat(),
        })
    except Exception as e:
        logger.error(f"[API] /api/signals/history failed: {e}")
        return jsonify({"error": str(e), "history": []}), 500


@app.route("/api/trading/equity")
def equity_curve():
    """Get equity curve and Sharpe ratio."""
    from utils.pnl_database import get_equity_curve, get_sharpe_ratio
    return jsonify({
        "curve": get_equity_curve(),
        "sharpe": get_sharpe_ratio(),
    })


@app.route("/api/trading/performance")
def phase_performance():
    """Get PnL breakdown by phase."""
    from utils.pnl_database import get_phase_performance
    return jsonify({"phases": get_phase_performance()})


# =============================================================================
# FARMING API ENDPOINTS (for Ned Discord bot integration)
# =============================================================================

@app.route('/api/farming/trigger', methods=['POST'])
def trigger_farming():
    """Manually trigger a Base farming action."""
    try:
        from strategies.base_farmer import run, get_farming_report
        data = request.get_json() or {}
        dry_run = data.get("dry_run", True)
        run(circuit_breaker_ok=True, dry_run=dry_run)
        report = get_farming_report()
        return jsonify({
            "status": "success",
            "message": "Farming action executed",
            "dry_run": dry_run,
            "report": report
        })
    except Exception as e:
        logger.error(f"[API] Farming trigger failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/farming/status', methods=['GET'])
def farming_status():
    """Get current farming status and quality report."""
    try:
        from strategies.base_farmer import get_farming_report
        report = get_farming_report()

        # Get airdrop status from airdrop_farmer
        from strategies.airdrop_farmer import AIRDROP_TARGETS

        # Format airdrop targets
        airdrop_status = []
        for key, data in AIRDROP_TARGETS.items():
            airdrop_status.append({
                "protocol": key,
                "token": data.get("token", ""),
                "status": data.get("status", ""),
                "tier": data.get("tier", ""),
                "est_value": data.get("est_value", ""),
            })

        # Sort by tier
        tier_order = {"S": 0, "A": 1, "B": 2, "F": 9}
        airdrop_status.sort(key=lambda x: tier_order.get(x.get("tier", ""), 5))

        return jsonify({
            "status": "success",
            "farming": report,
            "airdrops": airdrop_status,
        })
    except Exception as e:
        logger.error(f"[API] Farming status failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/farming/log', methods=['GET'])
def farming_log():
    """Get recent farming action log."""
    try:
        log_file = "/opt/slimy/pm_updown_bot_bundle/data/base_farming_log.json"
        if os.path.exists(log_file):
            with open(log_file, "r") as f:
                log = json.load(f)
            n = request.args.get("n", 10, type=int)
            return jsonify({
                "status": "success",
                "entries": log[-n:] if len(log) > n else log,
                "total": len(log)
            })
        return jsonify({"status": "success", "entries": [], "total": 0})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/farming/airdrop-targets', methods=['GET'])
def airdrop_targets():
    """Get tiered airdrop target list."""
    try:
        from strategies.airdrop_farmer import AIRDROP_TARGETS
        targets = {}
        for key, data in AIRDROP_TARGETS.items():
            targets[key] = {
                "token": data.get("token", ""),
                "status": data.get("status", ""),
                "tier": data.get("tier", ""),
                "note": data.get("action", ""),
                "est_value": data.get("est_value", ""),
            }
        return jsonify({"status": "success", "targets": targets})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8510, debug=False)
