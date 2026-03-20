#!/usr/bin/env python3
"""
Overnight Health Report for Trading Bot
Generates a summary of bot status, proof packs, and system health.
Run via cron or heartbeat for automated monitoring.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Configuration
BOT_DIR = Path("/opt/slimy/pm_updown_bot_bundle")
PROOF_DIR = Path("/tmp")
REPORT_FILE = Path("/home/slimy/ned-clawd/logs/overnight_report.json")
LOG_FILE = Path("/home/slimy/ned-clawd/logs/overnight.log")


def log(msg: str):
    """Log message to file and stdout."""
    timestamp = datetime.now().isoformat()
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, 'a') as f:
        f.write(line + "\n")


def check_gdelt_health() -> dict:
    """Check GDELT geopolitical signal health."""
    import subprocess

    cache_file = Path("/tmp/gdelt_signal_cache.json")
    signal = None

    # Try to read latest cached signal
    if cache_file.exists():
        try:
            signal = json.loads(cache_file.read_text())
        except Exception:
            pass

    if signal is None:
        return {
            "status": "no_data",
            "geo_risk_score": None,
            "event_count": 0,
            "cached": False,
        }

    # Check if signal is stale (>24h)
    timestamp_str = signal.get("timestamp", "")
    stale = True
    try:
        if timestamp_str:
            sig_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - sig_time).total_seconds() / 3600
            stale = age_hours > 24
    except Exception:
        stale = True

    return {
        "status": "stale" if stale else "fresh",
        "geo_risk_score": signal.get("geo_risk_score"),
        "event_count": signal.get("event_count", 0),
        "avg_tone": signal.get("avg_tone"),
        "regions": signal.get("regions", {}),
        "cached": signal.get("cached", False),
        "timestamp": timestamp_str,
        "stale": stale,
    }


def check_proof_packs() -> dict:
    """Check recent proof packs."""
    log("Checking proof packs...")
    
    proofs = []
    for f in PROOF_DIR.glob("proof_*.json"):
        age = datetime.now() - datetime.fromtimestamp(f.stat().st_mtime)
        proofs.append({
            "file": f.name,
            "age_hours": round(age.total_seconds() / 3600, 2),
            "size": f.stat().st_size
        })
    
    # Sort by age
    proofs.sort(key=lambda x: x["age_hours"])
    
    # Check for stale proofs (>24h)
    stale = [p for p in proofs if p["age_hours"] > 24]
    
    return {
        "total": len(proofs),
        "recent_24h": len([p for p in proofs if p["age_hours"] <= 24]),
        "stale": len(stale),
        "latest": proofs[0] if proofs else None,
        "stale_list": [p["file"] for p in stale]
    }


def check_system_health() -> dict:
    """Check NUC1 system health."""
    log("Checking system health...")
    
    import subprocess
    
    # Disk
    result = subprocess.run(
        ["df", "-h", "/"], capture_output=True, text=True
    )
    lines = result.stdout.strip().split("\n")
    disk = "0"
    if len(lines) > 1:
        parts = lines[-1].split()
        for p in parts:
            if "%" in p:
                disk = p.replace("%", "")
                break
    
    # Memory
    result = subprocess.run(
        ["free", "-h"], capture_output=True, text=True
    )
    mem_lines = result.stdout.strip().split("\n")
    mem_used = "0"
    if len(mem_lines) > 1:
        parts = mem_lines[1].split()
        if len(parts) >= 3:
            mem_used = parts[2] if "Gi" in parts[1] else parts[2]
    
    # Load
    result = subprocess.run(
        ["uptime"], capture_output=True, text=True
    )
    uptime_parts = result.stdout.split()
    load = "0.0"
    if len(uptime_parts) >= 13:
        load = uptime_parts[9].replace(",", "")
    
    # Zombies
    result = subprocess.run(
        ["ps", "aux"], capture_output=True, text=True
    )
    zombies = result.stdout.count(" Z ")
    
    return {
        "disk_used_pct": int(disk) if disk.isdigit() else 0,
        "memory_used_gb": mem_used,
        "load_1m": float(load) if load.replace(".", "").isdigit() else 0.0,
        "zombies": zombies
    }


def check_gateway() -> dict:
    """Check OpenClaw gateway status."""
    log("Checking gateway...")
    
    import subprocess
    result = subprocess.run(
        ["openclaw", "gateway", "status"], capture_output=True, text=True
    )
    
    return {
        "status": "running" if result.returncode == 0 else "stopped",
        "output": result.stdout[:200] if result.stdout else result.stderr[:200]
    }


def check_git_status() -> dict:
    """Check git status for uncommitted changes."""
    log("Checking git status...")
    
    import subprocess
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=BOT_DIR, capture_output=True, text=True
    )
    
    uncommitted = len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0
    
    result = subprocess.run(
        ["git", "log", "--oneline", "-1", "--format=%H %s"], 
        cwd=BOT_DIR, capture_output=True, text=True
    )
    latest_commit = result.stdout.strip() if result.stdout else "unknown"
    
    return {
        "uncommitted_files": uncommitted,
        "latest_commit": latest_commit
    }


def generate_report() -> dict:
    """Generate overnight health report."""
    log("Generating overnight report...")
    
    report = {
        "timestamp": datetime.now().isoformat(),
        "bot_dir": str(BOT_DIR),
        "proofs": check_proof_packs(),
        "gdelt": check_gdelt_health(),
        "system": check_system_health(),
        "gateway": check_gateway(),
        "git": check_git_status(),
        "status": "healthy"
    }
    
    # Determine overall status
    if report["proofs"]["stale"] > 0:
        report["status"] = "warning"
    if report["gdelt"].get("stale", False):
        report["status"] = "warning"
    if report["system"]["disk_used_pct"] > 80:
        report["status"] = "warning"
    if report["gateway"]["status"] != "running":
        report["status"] = "critical"
    
    return report


def save_report(report: dict):
    """Save report to file."""
    with open(REPORT_FILE, 'w') as f:
        json.dump(report, f, indent=2)
    log(f"Report saved to {REPORT_FILE}")


def print_summary(report: dict):
    """Print human-readable summary."""
    print("\n" + "=" * 50)
    print("OVERNIGHT HEALTH REPORT")
    print("=" * 50)
    print(f"Time: {report['timestamp']}")
    print(f"Status: {report['status'].upper()}")
    print()
    
    print("📊 Proof Packs:")
    print(f"  Total: {report['proofs']['total']}")
    print(f"  Recent (24h): {report['proofs']['recent_24h']}")
    print(f"  Stale (>24h): {report['proofs']['stale']}")
    if report['proofs']['stale_list']:
        print(f"  Stale files: {', '.join(report['proofs']['stale_list'][:5])}")

    print()
    print("🌍 GDELT Geopolitical Signal:")
    g = report.get('gdelt', {})
    status_icon = "✓" if g.get('status') == 'fresh' else "⚠"
    print(f"  {status_icon} Status: {g.get('status', 'unknown')}")
    if g.get('geo_risk_score') is not None:
        print(f"  Risk Score: {g.get('geo_risk_score'):.4f}")
        print(f"  Event Count: {g.get('event_count', 0)}")
        print(f"  Avg Tone: {g.get('avg_tone', 0):.4f}")
        if g.get('regions'):
            print(f"  Regions: {g.get('regions')}")
    else:
        print(f"  Risk Score: no data")
    if g.get('timestamp'):
        print(f"  Timestamp: {g.get('timestamp')[:19]}")

    print()
    print("🖥️ System Health:")
    s = report['system']
    print(f"  Disk: {s['disk_used_pct']}% used")
    print(f"  Memory: {s['memory_used_gb']} GB")
    print(f"  Load: {s['load_1m']}")
    print(f"  Zombies: {s['zombies']}")
    
    print()
    print("🌐 Gateway:")
    print(f"  Status: {report['gateway']['status']}")
    
    print()
    print("📝 Git:")
    print(f"  Uncommitted: {report['git']['uncommitted_files']}")
    print(f"  Latest: {report['git']['latest_commit'][:40]}...")
    
    print()
    print("=" * 50)


def main():
    """Main entry point."""
    log("=" * 50)
    log("Starting overnight health report...")
    
    # Ensure logs directory exists
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    report = generate_report()
    save_report(report)
    print_summary(report)
    
    log(f"Report complete. Status: {report['status']}")
    log("=" * 50)
    
    # Exit with appropriate code
    if report['status'] == 'critical':
        sys.exit(2)
    elif report['status'] == 'warning':
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
