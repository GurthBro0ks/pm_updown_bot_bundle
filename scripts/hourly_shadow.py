#!/usr/bin/env python3
"""
Hourly Shadow Runner with Telegram PnL Notification
Run via cron: 0 * * * * python3 /opt/slimy/pm_updown_bot_bundle/scripts/hourly_shadow.py
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Configuration
BOT_DIR = Path("/opt/slimy/pm_updown_bot_bundle")
PROOF_DIR = Path("/tmp")
LOG_FILE = Path("/home/slimy/ned-clawd/logs/hourly_shadow.log")
CONFIG_FILE = Path("/home/slimy/ned-clawd/config/hourly_shadow.json")


def log(msg: str):
    """Log message."""
    timestamp = datetime.now().isoformat()
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, 'a') as f:
        f.write(line + "\n")


def load_config() -> dict:
    """Load configuration."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {
        "venue": "kalshi",
        "telegram_enabled": False,
        "telegram_channel": None,
        "min_profit_for_alert": 0.01
    }


def save_config(config: dict):
    """Save configuration."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


def run_shadow() -> dict:
    """Run shadow mode and capture results."""
    log("Running hourly shadow...")
    
    env = os.environ.copy()
    
    # Add Kalshi credentials if available
    if os.environ.get("KALSHI_KEY"):
        env["KALSHI_KEY"] = os.environ["KALSHI_KEY"]
    if os.environ.get("KALSHI_SECRET"):
        env["KALSHI_SECRET"] = os.environ["KALSHI_SECRET"]
    
    result = subprocess.run(
        [sys.executable, str(BOT_DIR / "runner.py"), 
         "--mode", "shadow", 
         "--venue", "kalshi"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env
    )
    
    # Extract key metrics from output
    output = result.stdout + result.stderr
    
    proof_file = None
    for f in PROOF_DIR.glob("proof_ned_risk_*.json"):
        if proof_file is None or f.stat().st_mtime > proof_file.stat().st_mtime:
            proof_file = f
    
    return {
        "success": result.returncode == 0,
        "output": output,
        "proof_file": str(proof_file) if proof_file else None,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


def extract_pnl(output: str) -> dict:
    """Extract PnL from output."""
    pnl = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
    
    for line in output.split("\n"):
        if "Trade" in line and "PnL:" in line:
            pnl["trades"] += 1
            if "WIN" in line:
                pnl["wins"] += 1
            elif "LOSS" in line:
                pnl["losses"] += 1
        if "PnL: $" in line:
            try:
                amount = line.split("PnL: $")[1].split()[0]
                pnl["pnl"] += float(amount.replace("$", "").replace(",", ""))
            except:
                pass
    
    return pnl


def format_telegram_message(result: dict, pnl: dict) -> str:
    """Format Telegram notification message."""
    status = "✅" if result["success"] else "❌"
    
    msg = f"{status} Hourly Shadow Report\n"
    msg += f"Time: {result['timestamp'][:19]}\n"
    msg += f"Trades: {pnl['trades']} | Wins: {pnl['wins']} | Losses: {pnl['losses']}\n"
    msg += f"PnL: ${pnl['pnl']:.4f}"
    
    if result["proof_file"]:
        msg += f"\nProof: {result['proof_file'].split('/')[-1]}"
    
    return msg


def send_telegram_message(message: str, channel: str = None):
    """Send Telegram message via OpenClaw."""
    from openclaw.message import send
    
    config = load_config()
    target = channel or config.get("telegram_channel")
    
    if not target:
        log("No Telegram channel configured")
        return False
    
    try:
        send(
            action="send",
            target=target,
            message=message,
            channel="telegram"
        )
        log("Telegram message sent")
        return True
    except Exception as e:
        log(f"Telegram send failed: {e}")
        return False


def main():
    """Main entry point."""
    log("=" * 50)
    log("Starting hourly shadow run...")
    
    # Load config
    config = load_config()
    log(f"Config: venue={config['venue']}, telegram={config['telegram_enabled']}")
    
    # Run shadow
    result = run_shadow()
    
    # Extract PnL
    pnl = extract_pnl(result["output"])
    log(f"Shadow result: success={result['success']}, trades={pnl['trades']}, pnl=${pnl['pnl']:.4f}")
    
    # Save result
    result_file = PROOF_DIR / f"hourly_shadow_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(result_file, 'w') as f:
        json.dump({
            "result": result,
            "pnl": pnl,
            "config": config
        }, f, indent=2)
    
    # Send Telegram if enabled and profitable
    if config.get("telegram_enabled"):
        message = format_telegram_message(result, pnl)
        
        # Only alert on significant PnL
        if pnl["pnl"] >= config.get("min_profit_for_alert", 0.01):
            send_telegram_message(message)
        elif pnl["trades"] > 0:
            log(f"PnL ${pnl['pnl']:.4f} below threshold, no alert")
    
    log("Hourly shadow complete")
    log("=" * 50)
    
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
