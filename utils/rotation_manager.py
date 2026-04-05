#!/usr/bin/env python3
"""
Rotation & Risk Manager
Inspired by @nobrainflip's 2026 playbook:
- Rotation detection
- Invalidation rules
- Auto profit-taking
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# Config file
CONFIG_PATH = Path("/opt/slimy/pm_updown_bot_bundle/config/rotation_config.json")

# Default config
DEFAULT_CONFIG = {
    # Invalidation rules - exit if these trigger
    "invalidation": {
        "max_hold_hours": 168,      # Max 7 days per position
        "sentiment_drop_pct": 0.20,  # Exit if sentiment drops 20%
        "volume_drop_pct": 0.50,     # Exit if volume halves
        "stop_loss_pct": 0.10,       # Hard stop at -10%
    },
    # Profit-taking levels
    "profit_tiers": [
        {"pct": 1.5, "take_pct": 0.50},   # At 1.5x, take 50%
        {"pct": 2.0, "take_pct": 0.25},   # At 2x, take 25%
        {"pct": 3.0, "take_pct": 0.75},   # At 3x, take 75%
        {"pct": 5.0, "take_pct": 1.00},   # At 5x, take all
    ],
    # Position sizing (3-layer stack)
    "position_stack": {
        "beta": {"pct": 0.50, "description": "Liquid leader (SPY, BTC)"},
        "pick_shovel": {"pct": 0.30, "description": "Infra that wins either way"},
        "flyers": {"pct": 0.20, "description": "Small caps with narrative fit"},
    },
    # Rotation tracking
    "current_rotation": "value",  # value, casino, dump, extraction
    "rotation_meta": {
        "value": {"description": "Revenue, buybacks, real cashflow"},
        "casino": {"description": "Memes, NFTs, high momentum"},
        "dump": {"description": "After casino dumps, rotation out"},
        "extraction": {"description": "ICOs, structured launches"},
    },
    # Max positions
    "max_positions": 5,
    "max_daily_trades": 10,
}

class RotationManager:
    """Manages rotation detection, invalidation, and profit-taking"""
    
    def __init__(self, config_path: Path = CONFIG_PATH):
        self.config_path = config_path
        self.config = self._load_config()
        
    def _load_config(self) -> dict:
        """Load or create config"""
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text())
            except:
                pass
        # Create default
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
        return DEFAULT_CONFIG
    
    def save_config(self):
        """Save config"""
        self.config_path.write_text(json.dumps(self.config, indent=2))
    
    def check_invalidation(self, position: dict, current_sentiment: float = None) -> tuple[bool, str]:
        """
        Check if position should be exited due to invalidation.
        Returns (should_exit, reason)
        """
        inv = self.config["invalidation"]
        now = datetime.now(timezone.utc)
        
        # 1. Max hold time
        entry_time = position.get("entry_time", "")
        if entry_time:
            try:
                # Parse ISO timestamp
                if "Z" in entry_time:
                    entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                else:
                    entry_dt = datetime.fromisoformat(entry_time)
                
                hours_held = (now - entry_dt).total_seconds() / 3600
                if hours_held > inv["max_hold_hours"]:
                    return True, f"max_hold_time ({hours_held:.0f}h > {inv['max_hold_hours']}h)"
            except:
                pass
        
        # 2. Sentiment drop (if we have current sentiment)
        if current_sentiment and "entry_sentiment" in position:
            entry_sentiment = position.get("entry_sentiment", 1.0)
            drop_pct = (entry_sentiment - current_sentiment) / entry_sentiment
            if drop_pct > inv["sentiment_drop_pct"]:
                return True, f"sentiment_drop ({drop_pct:.1%} > {inv['sentiment_drop_pct']:.0%})"
        
        # 3. Stop loss
        if "entry_price" in position and "current_price" in position:
            entry = position["entry_price"]
            current = position["current_price"]
            loss_pct = (entry - current) / entry
            if loss_pct > inv["stop_loss_pct"]:
                return True, f"stop_loss ({loss_pct:.1%} > {inv['stop_loss_pct']:.0%})"
        
        return False, ""
    
    def check_profit_taking(self, position: dict) -> tuple[bool, float]:
        """
        Check if we should take profit.
        Returns (should_take, pct_to_take)
        """
        if "entry_price" not in position or "current_price" not in position:
            return False, 0.0
        
        entry = position["entry_price"]
        current = position["current_price"]
        gain_pct = (current - entry) / entry
        
        for tier in self.config["profit_tiers"]:
            if gain_pct >= tier["pct"]:
                return True, tier["take_pct"]
        
        return False, 0.0
    
    def get_position_size(self, total_capital: float, layer: str = "flyers") -> float:
        """Get position size based on 3-layer stack"""
        stack = self.config["position_stack"]
        if layer not in stack:
            layer = "flyers"
        return total_capital * stack[layer]["pct"]
    
    def set_rotation(self, rotation: str):
        """Update current rotation meta"""
        if rotation in self.config["rotation_meta"]:
            self.config["current_rotation"] = rotation
            self.save_config()
    
    def get_rotation(self) -> str:
        """Get current rotation"""
        return self.config.get("current_rotation", "value")
    
    def get_recommendation(self) -> str:
        """Get trade recommendation based on current rotation"""
        rotation = self.get_rotation()
        meta = self.config["rotation_meta"][rotation]
        
        recommendations = {
            "value": "Focus: revenue tokens, buyback tokens, cashflow plays. Exit: memes, high-timeframe specs.",
            "casino": "Focus: momentum, memes, narrative coins. Set stop losses. Take profits fast.",
            "dump": "Exit everything. Hold cash. Wait for bottom.",
            "extraction": "Focus: structured launches, points, ICOs. Be careful of FDV.",
        }
        
        return f"[{rotation.upper()}] {meta['description']}\n{recommendations.get(rotation, 'Unknown')}"


def main():
    """CLI for rotation manager"""
    import argparse
    parser = argparse.ArgumentParser(description="Rotation & Risk Manager")
    parser.add_argument("--check", type=str, help="Check position for invalidation")
    parser.add_argument("--rotation", type=str, choices=["value", "casino", "dump", "extraction"], help="Set rotation")
    parser.add_argument("--recommend", action="store_true", help="Get recommendation")
    parser.add_argument("--config", action="store_true", help="Show config")
    args = parser.parse_args()
    
    rm = RotationManager()
    
    if args.rotation:
        rm.set_rotation(args.rotation)
        print(f"Rotation set to: {args.rotation}")
    
    if args.recommend:
        print(rm.get_recommendation())
    
    if args.config:
        print(json.dumps(rm.config, indent=2))
    
    if not any([args.rotation, args.recommend, args.config]):
        parser.print_help()


if __name__ == "__main__":
    main()
