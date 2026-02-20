#!/usr/bin/env python3
\"\"\" Ned - OpenClaw Autonomous System Optimizer
Monitors trading systems, learns from results, and makes intelligent adjustments to maximize performance while respecting safety constraints.
\"\"\"

import sqlite3
import json
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging
import subprocess
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - NED - %(levelname)s - %(message)s'
)
logger = logging.getLogger('ned')

BUNDLE_DIR = Path("/opt/slimy/pm_updown_bot_bundle")
DB_PATH = BUNDLE_DIR / "data" / "performance.db"
CONFIG_PATH = BUNDLE_DIR / "config"
LOGS_DIR = BUNDLE_DIR / "logs"

MAX_POSITION_SIZE = 1000
MAX_TOTAL_EXPOSURE = 5000
MIN_EDGE_BPS = 20
MAX_KELLY_FRACTION = 0.5
MIN_KELLY_FRACTION = 0.1

class NedOptimizer:
    def __init__(self):
        self.session_id = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.audit_log = []
        self.optimizations_made = []

    def log_decision(self, decision: str, rationale: str, data: Dict):
        entry = {
            'timestamp': datetime.now().isoformat(),
            'decision': decision,
            'rationale': rationale,
            'data': data,
            'session_id': self.session_id
        }
        self.audit_log.append(entry)
        audit_file = BUNDLE_DIR / "logs" / "ned_audit" / f"audit_{self.session_id}.json"
        audit_file.parent.mkdir(exist_ok=True, parents=True)
        with open(audit_file, 'w') as f:
            json.dump(self.audit_log, f, indent=2)
        logger.info(f"DECISION: {decision}")
        logger.info(f"RATIONALE: {rationale}")

    def analyze_trade_performance(self, venue: str, hours: int = 24) -> Dict:
        if not DB_PATH.exists():
            logger.warning("Performance database not found")
            return {}
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        c.execute('''
            SELECT COUNT(*) as total_opps,
                   SUM(CASE WHEN executed = 1 THEN 1 ELSE 0 END) as executed,
                   AVG(expected_roi) as avg_roi,
                   AVG(edge_bps) as avg_edge,
                   MIN(edge_bps) as min_edge,
                   MAX(edge_bps) as max_edge,
                   SUM(pnl_usd) as total_pnl
            FROM opportunities
            WHERE timestamp > ? AND platform = ?
        ''', (cutoff, venue))
        result = c.fetchone()
        conn.close()
        if not result or result[0] == 0:
            return {'status': 'insufficient_data'}
        metrics = {
            'status': 'valid',
            'total_opportunities': result[0],
            'executed_trades': result[1] or 0,
            'execution_rate': (result[1] or 0) / result[0] if result[0] > 0 else 0,
            'avg_roi': result[2] or 0,
            'avg_edge_bps': result[3] or 0,
            'min_edge_bps': result[4] or 0,
            'max_edge_bps': result[5] or 0,
            'total_pnl': result[6] or 0
        }
        return metrics

    def optimize_edge_threshold(self, venue: str, metrics: Dict) -> Optional[int]:
        if metrics.get('status') != 'valid':
            return None
        current_config = self.load_config(venue)
        current_edge = current_config.get('min_edge_bps', 30)
        exec_rate = metrics['execution_rate']
        avg_edge = metrics['avg_edge_bps']
        total_opps = metrics['total_opportunities']
        if total_opps < 10:
            self.log_decision(
                "edge_threshold_unchanged",
                f"Insufficient data ({total_opps} opps)",
                {'venue': venue, 'current_edge': current_edge}
            )
            return None
        if exec_rate < 0.1:
            new_edge = max(MIN_EDGE_BPS, int(current_edge * 0.8))
            self.log_decision(
                "lower_edge_threshold",
                f"Execution rate too low ({exec_rate:.1%}), lowering threshold",
                {'venue': venue, 'old_edge': current_edge, 'new_edge': new_edge}
            )
            return new_edge
        if exec_rate > 0.8:
            new_edge = min(100, int(current_edge * 1.2))
            self.log_decision(
                "raise_edge_threshold",
                f"Execution rate high ({exec_rate:.1%}), raising threshold",
                {'venue': venue, 'old_edge': current_edge, 'new_edge': new_edge}
            )
            return new_edge
        if avg_edge > current_edge * 2 and total_opps > 20:
            new_edge = min(100, int(avg_edge * 0.7))
            self.log_decision(
                "optimize_edge_upward",
                f"Avg edge ({avg_edge:.0f}bps) >> threshold ({current_edge}bps)",
                {'venue': venue, 'old_edge': current_edge, 'new_edge': new_edge}
            )
            return new_edge
        self.log_decision(
            "edge_threshold_optimal",
            f"Threshold ({current_edge}bps) optimal",
            {'venue': venue, 'exec_rate': exec_rate}
        )
        return None

    def optimize_kelly_fraction(self, venue: str, metrics: Dict) -> Optional[float]:
        if metrics.get('status') != 'valid':
            return None
        current_config = self.load_config(venue)
        current_kelly = current_config.get('kelly_fraction', 0.25)
        if metrics['executed_trades'] < 5:
            return None
        actual_pnl = metrics['total_pnl']
        executed = metrics['executed_trades']
        if actual_pnl > 0 and executed >= 10:
            new_kelly = min(MAX_KELLY_FRACTION, current_kelly * 1.1)
            if new_kelly != current_kelly:
                self.log_decision(
                    "increase_kelly_fraction",
                    f"Profitable (${actual_pnl:.2f}), increasing Kelly",
                    {'venue': venue, 'old_kelly': current_kelly, 'new_kelly': new_kelly}
                )
                return new_kelly
        elif actual_pnl < 0 and executed >= 10:
            new_kelly = max(MIN_KELLY_FRACTION, current_kelly * 0.8)
            self.log_decision(
                "decrease_kelly_fraction",
                f"Losses (${actual_pnl:.2f}), decreasing Kelly",
                {'venue': venue, 'old_kelly': current_kelly, 'new_kelly': new_kelly}
            )
            return new_kelly
        return None

    def check_cron_health(self) -> List[Dict]:
        issues = []
        kalshi_logs = list((LOGS_DIR / "yesno_kalshi").glob("scan_*.log"))
        if kalshi_logs:
            latest = max(kalshi_logs, key=lambda p: p.stat().st_mtime)
            age_minutes = (datetime.now().timestamp() - latest.stat().st_mtime) / 60
            if age_minutes > 20:
                issues.append({
                    'type': 'cron_stale',
                    'job': 'kalshi_yesno',
                    'last_run_minutes_ago': age_minutes,
                    'expected_interval': 15
                })
                self.log_decision(
                    "cron_health_issue",
                    f"Kalshi cron stale ({age_minutes:.0f} min)",
                    {'job': 'kalshi_yesno', 'age': age_minutes}
                )
        self.log_decision("cron_health_ok", "All cron jobs healthy", {'issues': len(issues)})
        return issues

    def fix_cron_issue(self, issue: Dict):
        if issue['type'] == 'cron_stale':
            job_name = issue['job']
            logger.warning(f"Restarting {job_name} cron job")
            if job_name == 'kalshi_yesno':
                script = BUNDLE_DIR / "scripts" / "cron_yesno_kalshi.sh"
                try:
                    subprocess.run([str(script)], check=True)
                    self.log_decision("cron_manually_triggered", f"Triggered {job_name}", issue)
                except subprocess.CalledProcessError as e:
                    logger.error(f"Cron trigger failed: {e}")

    def load_config(self, venue: str) -> Dict:
        config_files = {
            'kalshi': CONFIG_PATH / "kalshi_strategy.json",
            'ibkr': CONFIG_PATH / "ibkr_paper.json"
        }
        config_file = config_files.get(venue)
        if not config_file or not config_file.exists():
            return {}
        with open(config_file) as f:
            return json.load(f)

    def save_config(self, venue: str, config: Dict):
        config_files = {
            'kalshi': CONFIG_PATH / "kalshi_strategy.json",
            'ibkr': CONFIG_PATH / "ibkr_paper.json"
        }
        config_file = config_files.get(venue)
        if not config_file:
            return
        if config_file.exists():
            backup_dir = CONFIG_PATH / "backups"
            backup_dir.mkdir(exist_ok=True)
            backup_file = backup_dir / f"{venue}_backup_{self.session_id}.json"
            config_file.rename(backup_file)
            logger.info(f"Backed up config to {backup_file}")
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)
        logger.info(f"Updated {venue} configuration")
        self.optimizations_made.append({'venue': venue, 'timestamp': datetime.now().isoformat(), 'config_file': str(config_file)})

    def optimize_venue(self, venue: str):
        logger.info(f" {'='*70}")
        logger.info(f"OPTIMIZING: {venue.upper()}")
        logger.info(f"{'='*70}")
        metrics = self.analyze_trade_performance(venue, hours=24)
        if metrics.get('status') != 'valid':
            logger.info(f"Insufficient data for {venue}")
            return
        logger.info(f"Opportunities: {metrics['total_opportunities']}")
        logger.info(f"Executed: {metrics['executed_trades']}")
        logger.info(f"Execution rate: {metrics['execution_rate']:.1%}")
        logger.info(f"Avg edge: {metrics['avg_edge_bps']:.0f} bps")
        logger.info(f"Total P&L: ${metrics['total_pnl']:.2f}")
        config = self.load_config(venue)
        if not config:
            logger.warning(f"No config for {venue}")
            return
        config_changed = False
        new_edge = self.optimize_edge_threshold(venue, metrics)
        if new_edge:
            config['parameters']['min_edge_bps'] = new_edge
            config_changed = True
        new_kelly = self.optimize_kelly_fraction(venue, metrics)
        if new_kelly:
            config['parameters']['kelly_fraction'] = new_kelly
            config_changed = True
        if config_changed:
            self.save_config(venue, config)
            logger.info(f"✓ Config updated for {venue}")
        else:
            logger.info(f"✓ No optimization needed for {venue}")

    def run_optimization_cycle(self):
        logger.info(" " + "="*70)
        logger.info("NED AUTONOMOUS OPTIMIZER - STARTING CYCLE")
        logger.info(f"Session: {self.session_id}")
        logger.info("="*70 + " ")
        logger.info("Checking cron job health...")
        issues = self.check_cron_health()
        for issue in issues:
            self.fix_cron_issue(issue)
        if not issues:
            logger.info("✓ All cron jobs healthy")
        for venue in ['kalshi', 'ibkr']:
            try:
                self.optimize_venue(venue)
            except Exception as e:
                logger.error(f"Error optimizing {venue}: {e}")
                import traceback
                traceback.print_exc()
        logger.info(" " + "="*70)
        logger.info("OPTIMIZATION CYCLE COMPLETE")
        logger.info("="*70)
        logger.info(f"Session: {self.session_id}")
        logger.info(f"Optimizations made: {len(self.optimizations_made)}")
        logger.info(f"Audit log entries: {len(self.audit_log)}")
        logger.info(f"Audit file: logs/ned_audit/audit_{self.session_id}.json")
        logger.info("="*70 + " ")

def main():
    ned = NedOptimizer()
    ned.run_optimization_cycle()

if __name__ == '__main__':
    main()
EOFNED && mkdir -p logs/ned_optimizer logs/ned_audit && chmod +x scripts/ned_optimizer.py && echo "ned_optimizer.py created ✓"