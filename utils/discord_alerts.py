#!/usr/bin/env python3
"""
Discord Webhook Alerts Module
Rich embeds with color coding, structured fields, and Discord timestamps.
"""

import os
import json
import requests
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ============================================================================
# Webhook URLs (from environment)
# ============================================================================

DISCORD_WH_TRADE_JOURNAL = os.getenv("DISCORD_WH_TRADE_JOURNAL", "")
DISCORD_WH_NED_STATUS = os.getenv("DISCORD_WH_NED_STATUS", "")
DISCORD_WH_STOCK_ALERTS = os.getenv("DISCORD_WH_STOCK_ALERTS", "")
DISCORD_WH_CRYPTO_MONITOR = os.getenv("DISCORD_WH_CRYPTO_MONITOR", "")
DISCORD_WH_KALSHI_SIGNALS = os.getenv("DISCORD_WH_KALSHI_SIGNALS", "")
DISCORD_WH_AIRDROP_TRACKER = os.getenv("DISCORD_WH_AIRDROP_TRACKER", "")
DISCORD_WH_WALLET_TRACKER = os.getenv("DISCORD_WH_WALLET_TRACKER", "")
DISCORD_WH_NED_COMMANDS = os.getenv("DISCORD_WH_NED_COMMANDS", "")
DISCORD_WH_DAILY_REPORT = os.getenv("DISCORD_WH_DAILY_REPORT", "")
DISCORD_WH_WEATHER_SIGNALS = os.getenv("DISCORD_WH_WEATHER_SIGNALS", "")
DISCORD_WH_SENTIMENT_FEED = os.getenv("DISCORD_WH_SENTIMENT_FEED", "")
DISCORD_WH_DATA_DUMPS = os.getenv("DISCORD_WH_DATA_DUMPS", "")

# Execution mode for footer
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "shadow")
BOT_NAME = "Ned Carlson"

# ============================================================================
# Color Constants (decimal)
# ============================================================================

COLOR_PROFIT = 0x00FF7F      # Green - profit
COLOR_LOSS = 0xFF4444        # Red - loss
COLOR_WARNING = 0xFFA500      # Orange - warning
COLOR_AIRDROP = 0x9B59B6     # Purple - airdrop
COLOR_CRYPTO = 0x7289DA      # Blurple - crypto
COLOR_STOCK = 0x3498DB       # Blue - stocks
COLOR_KALSHI = 0xE74C3C      # Red - kalshi
COLOR_INFO = 0x95A5A6        # Gray - info
COLOR_SUCCESS = 0x2ECC71     # Green - success

# ============================================================================
# Helper Functions
# ============================================================================

def _get_timestamp(unix_time: Optional[int] = None) -> str:
    """Get Discord timestamp string"""
    if unix_time is None:
        unix_time = int(datetime.now(timezone.utc).timestamp())
    return f"<t:{unix_time}:R>"


def _get_mode() -> str:
    """Get execution mode for footer"""
    return EXECUTION_MODE.lower()


def _send_webhook(url: str, payload: Dict[str, Any]) -> bool:
    """Send webhook payload to Discord"""
    if not url:
        logger.debug("Webhook URL not configured, skipping")
        return False

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 204:
            logger.debug("Webhook sent successfully")
            return True
        elif response.status_code == 429:
            logger.warning("Webhook rate limited")
            return False
        else:
            logger.warning(f"Webhook failed: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return False


def _build_embed(
    title: str,
    description: str = "",
    color: int = COLOR_INFO,
    fields: Optional[List[Dict[str, str]]] = None,
    footer: Optional[str] = None,
    thumbnail: Optional[str] = None,
    author: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build a Discord embed object"""
    embed = {
        "title": title,
        "color": color,
    }

    if description:
        embed["description"] = description

    if fields:
        embed["fields"] = [
            {"name": f["name"], "value": f["value"], "inline": f.get("inline", False)}
            for f in fields
        ]

    # Footer with bot name and mode
    footer_text = footer or f"{BOT_NAME} | {_get_mode().upper()}"
    embed["footer"] = {"text": footer_text}

    # Timestamp
    embed["timestamp"] = datetime.now(timezone.utc).isoformat()

    if thumbnail:
        embed["thumbnail"] = {"url": thumbnail}

    if author:
        embed["author"] = author

    return embed


# ============================================================================
# Alert Functions
# ============================================================================

def send_status_alert(
    phase: str,
    status: str,
    details: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> bool:
    """
    Send status update to #ned-status channel.
    """
    fields = []

    if details:
        for key, value in details.items():
            if isinstance(value, float):
                fields.append({"name": key, "value": f"${value:.2f}", "inline": True})
            else:
                fields.append({"name": key, "value": str(value), "inline": True})

    if error:
        fields.append({"name": "Error", "value": error, "inline": False})

    color = COLOR_SUCCESS if status == "success" else COLOR_WARNING

    embed = _build_embed(
        title=f"📊 Phase {phase} {status.title()}",
        description=f"Status update at {_get_timestamp()}",
        color=color,
        fields=fields,
    )

    payload = {"embeds": [embed]}
    return _send_webhook(DISCORD_WH_NED_STATUS, payload)


def send_stock_alert(
    ticker: str,
    action: str,
    price: float,
    size_usd: float,
    sentiment: Optional[float] = None,
    pnl: Optional[float] = None,
    reason: Optional[str] = None,
) -> bool:
    """
    Send stock trade alert to #stock-alerts channel.
    Color: Green for buy, Red for sell
    """
    # Determine color based on action
    if action.lower() == "buy":
        color = COLOR_PROFIT
        emoji = "🟢"
    elif action.lower() == "sell":
        color = COLOR_LOSS if (pnl and pnl < 0) else COLOR_PROFIT
        emoji = "🔴"
    else:
        color = COLOR_STOCK
        emoji = "🔵"

    fields = [
        {"name": "Ticker", "value": f"**{ticker}**", "inline": True},
        {"name": "Action", "value": action.upper(), "inline": True},
        {"name": "Price", "value": f"${price:.2f}", "inline": True},
        {"name": "Size", "value": f"${size_usd:.2f}", "inline": True},
    ]

    if sentiment is not None:
        fields.append({"name": "Sentiment", "value": f"{sentiment:.2f}", "inline": True})

    if pnl is not None:
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        fields.append({"name": "P&L", "value": f"{pnl_emoji} ${pnl:+.2f}", "inline": True})

    if reason:
        fields.append({"name": "Reason", "value": reason, "inline": False})

    embed = _build_embed(
        title=f"{emoji} {ticker} ${price:.2f} | {action.upper()}",
        description=f"Stock alert at {_get_timestamp()}",
        color=color,
        fields=fields,
    )

    payload = {"embeds": [embed]}
    return _send_webhook(DISCORD_WH_STOCK_ALERTS, payload)


def send_crypto_alert(
    ticker: str,
    action: str,
    price: float,
    size_usd: float,
    venue: str = "polymarket",
    arb_opportunity: Optional[float] = None,
    pnl: Optional[float] = None,
) -> bool:
    """
    Send crypto trade alert to #crypto-monitor channel.
    Color: Blurple for crypto, Green/Red for profit/loss
    """
    if action.lower() == "buy":
        color = COLOR_CRYPTO
        emoji = "🟣"
    elif action.lower() == "sell":
        color = COLOR_LOSS if (pnl and pnl < 0) else COLOR_PROFIT
        emoji = "🔴"
    else:
        color = COLOR_CRYPTO
        emoji = "🔵"

    fields = [
        {"name": "Ticker", "value": f"**{ticker}**", "inline": True},
        {"name": "Action", "value": action.upper(), "inline": True},
        {"name": "Price", "value": f"${price:.4f}", "inline": True},
        {"name": "Venue", "value": venue.title(), "inline": True},
    ]

    if arb_opportunity is not None:
        arb_emoji = "⚡" if arb_opportunity > 1 else ""
        fields.append({"name": "Arb", "value": f"{arb_emoji} {arb_opportunity:.2f}%", "inline": True})

    if pnl is not None:
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        fields.append({"name": "P&L", "value": f"{pnl_emoji} ${pnl:+.2f}", "inline": True})

    embed = _build_embed(
        title=f"{emoji} {ticker} ${price:.4f} | {action.upper()}",
        description=f"Crypto alert at {_get_timestamp()}",
        color=color,
        fields=fields,
    )

    payload = {"embeds": [embed]}
    return _send_webhook(DISCORD_WH_CRYPTO_MONITOR, payload)


def send_kalshi_alert(
    market_id: str,
    question: str,
    action: str,
    odds: float,
    size_usd: float,
    expected_value: Optional[float] = None,
    liquidity: Optional[float] = None,
) -> bool:
    """
    Send Kalshi trade alert to #kalshi-signals channel.
    Color: Red for kalshi
    """
    color = COLOR_KALSHI

    if action.lower() == "buy":
        emoji = "🟢"
    elif action.lower() == "sell":
        emoji = "🔴"
    else:
        emoji = "🔵"

    fields = [
        {"name": "Market", "value": f"**{market_id}**", "inline": False},
        {"name": "Question", "value": question, "inline": False},
        {"name": "Action", "value": action.upper(), "inline": True},
        {"name": "Odds", "value": f"{odds:.2%}", "inline": True},
        {"name": "Size", "value": f"${size_usd:.2f}", "inline": True},
    ]

    if expected_value is not None:
        fields.append({"name": "EV", "value": f"{expected_value:.2%}", "inline": True})

    if liquidity is not None:
        fields.append({"name": "Liquidity", "value": f"${liquidity:.0f}", "inline": True})

    embed = _build_embed(
        title=f"{emoji} {market_id} {odds:.2%} | {action.upper()}",
        description=f"Kalshi alert at {_get_timestamp()}",
        color=color,
        fields=fields,
    )

    payload = {"embeds": [embed]}
    return _send_webhook(DISCORD_WH_KALSHI_SIGNALS, payload)


def send_airdrop_alert(
    action_type: str,
    protocol: str,
    details: Dict[str, Any],
    due_date: Optional[str] = None,
) -> bool:
    """
    Send airdrop alert to #airdrop-tracker channel.
    Color: Purple for airdrop
    """
    fields = [
        {"name": "Protocol", "value": f"**{protocol}**", "inline": True},
        {"name": "Action", "value": action_type.title(), "inline": True},
    ]

    for key, value in details.items():
        fields.append({"name": key, "value": str(value), "inline": True})

    if due_date:
        fields.append({"name": "Due", "value": due_date, "inline": True})

    emoji = "🔔" if action_type == "reminder" else "✅"

    embed = _build_embed(
        title=f"{emoji} Airdrop: {protocol}",
        description=f"Airdrop alert at {_get_timestamp()}",
        color=COLOR_AIRDROP,
        fields=fields,
    )

    payload = {"embeds": [embed]}
    return _send_webhook(DISCORD_WH_AIRDROP_TRACKER, payload)


def send_wallet_alert(balance_data: Dict[str, Any]) -> bool:
    """
    Send wallet balance alert to #wallet-tracker channel.
    Color: Blue for wallet info
    """
    wallet = balance_data.get("wallet", "Unknown")
    total_usd = balance_data.get("total_usd", 0)
    eth_price = balance_data.get("eth_price")
    timestamp = balance_data.get("timestamp", "")

    # Build fields for each chain
    fields = []
    for chain, data in balance_data.get("chains", {}).items():
        chain_name = chain.title()
        eth = data.get("eth", 0)
        eth_usd = data.get("eth_usd", 0)
        usdc = data.get("usdc", 0)
        chain_total = data.get("total_usd", 0)

        fields.append({
            "name": chain_name,
            "value": f"ETH: {eth:.4f} (${eth_usd:.2f})\nUSDC: ${usdc:.2f}\n**Total: ${chain_total:.2f}**",
            "inline": True
        })

    # Add total field
    fields.append({
        "name": "Portfolio Total",
        "value": f"**${total_usd:.2f}**",
        "inline": False
    })

    if eth_price:
        fields.append({
            "name": "ETH Price",
            "value": f"${eth_price:.2f}",
            "inline": True
        })

    embed = _build_embed(
        title=f"💰 Wallet Balance Update",
        description=f"Chain balances at {timestamp}",
        color=COLOR_STOCK,  # Blue
        fields=fields,
    )

    payload = {"embeds": [embed]}
    return _send_webhook(DISCORD_WH_WALLET_TRACKER, payload)


def send_health_alert(
    api_name: str,
    status: str,
    details: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> bool:
    """
    Send API health alert to #api-health channel.
    Color: Orange for warning, Green for healthy
    """
    color = COLOR_SUCCESS if status == "healthy" else COLOR_WARNING

    fields = [
        {"name": "API", "value": f"**{api_name}**", "inline": True},
        {"name": "Status", "value": status.title(), "inline": True},
    ]

    if details:
        for key, value in details.items():
            fields.append({"name": key, "value": str(value), "inline": True})

    if error:
        fields.append({"name": "Error", "value": error[:100], "inline": False})

    emoji = "✅" if status == "healthy" else "⚠️"

    embed = _build_embed(
        title=f"{emoji} API Health: {api_name}",
        description=f"Health check at {_get_timestamp()}",
        color=color,
        fields=fields,
    )

    payload = {"embeds": [embed]}
    return _send_webhook(DISCORD_WH_NED_STATUS, payload)


def send_weather_alert(
    location: str,
    condition: str,
    temperature: float,
    impact: Optional[str] = None,
    trading_impact: Optional[str] = None,
) -> bool:
    """
    Send weather alert to #weather-signals channel.
    Color: Info gray
    """
    emoji_map = {
        "sunny": "☀️",
        "cloudy": "☁️",
        "rain": "🌧️",
        "storm": "⛈️",
        "snow": "❄️",
        "wind": "💨",
    }

    emoji = emoji_map.get(condition.lower(), "🌡️")

    fields = [
        {"name": "Location", "value": location, "inline": True},
        {"name": "Condition", "value": f"{emoji} {condition.title()}", "inline": True},
        {"name": "Temp", "value": f"{temperature:.1f}°F", "inline": True},
    ]

    if impact:
        fields.append({"name": "Weather Impact", "value": impact, "inline": False})

    if trading_impact:
        fields.append({"name": "Trading Impact", "value": trading_impact, "inline": False})

    embed = _build_embed(
        title=f"{emoji} Weather: {location}",
        description=f"Weather update at {_get_timestamp()}",
        color=COLOR_INFO,
        fields=fields,
    )

    payload = {"embeds": [embed]}
    return _send_webhook(DISCORD_WH_WEATHER_SIGNALS, payload)


def send_sentiment_alert(
    ticker: str,
    sentiment_score: float,
    source: str,
    headline: str,
    sentiment_change: Optional[float] = None,
) -> bool:
    """
    Send sentiment alert to #sentiment-feed channel.
    Color: Blue for stocks
    """
    if sentiment_score >= 0.6:
        color = COLOR_PROFIT
        emoji = "📈"
    elif sentiment_score <= 0.4:
        color = COLOR_LOSS
        emoji = "📉"
    else:
        color = COLOR_STOCK
        emoji = "➡️"

    fields = [
        {"name": "Ticker", "value": f"**{ticker}**", "inline": True},
        {"name": "Score", "value": f"{sentiment_score:.2f}", "inline": True},
        {"name": "Source", "value": source, "inline": True},
    ]

    if sentiment_change is not None:
        change_emoji = "↑" if sentiment_change > 0 else "↓" if sentiment_change < 0 else "→"
        fields.append({"name": "Change", "value": f"{change_emoji} {sentiment_change:+.2f}", "inline": True})

    fields.append({"name": "Headline", "value": headline[:200], "inline": False})

    embed = _build_embed(
        title=f"{emoji} Sentiment: {ticker} ({sentiment_score:.2f})",
        description=f"Sentiment alert at {_get_timestamp()}",
        color=color,
        fields=fields,
    )

    payload = {"embeds": [embed]}
    return _send_webhook(DISCORD_WH_SENTIMENT_FEED, payload)


def send_daily_report(
    total_value: float,
    pnl: float,
    trades_today: int,
    positions: int,
    cash: float,
    phase_results: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Send daily report to #daily-report channel.
    Color: Green for profit, Red for loss
    """
    color = COLOR_PROFIT if pnl >= 0 else COLOR_LOSS

    fields = [
        {"name": "Total Value", "value": f"${total_value:.2f}", "inline": True},
        {"name": "P&L", "value": f"${pnl:+.2f}", "inline": True},
        {"name": "Cash", "value": f"${cash:.2f}", "inline": True},
        {"name": "Positions", "value": str(positions), "inline": True},
        {"name": "Trades Today", "value": str(trades_today), "inline": True},
    ]

    if phase_results:
        for phase, result in phase_results.items():
            status = "✅" if result else "❌"
            fields.append({"name": phase.upper(), "value": status, "inline": True})

    pnl_emoji = "📈" if pnl >= 0 else "📉"

    embed = _build_embed(
        title=f"{pnl_emoji} Daily Report: ${pnl:+.2f}",
        description=f"Report for {_get_timestamp()}",
        color=color,
        fields=fields,
    )

    payload = {"embeds": [embed]}
    return _send_webhook(DISCORD_WH_DAILY_REPORT, payload)


def send_test_webhook(channel_name: str, url: str) -> bool:
    """Send a test embed to verify webhook configuration."""
    embed = _build_embed(
        title="✅ Test Webhook",
        description=f"This is a test message from {BOT_NAME}",
        color=COLOR_SUCCESS,
        fields=[
            {"name": "Status", "value": "Connected", "inline": True},
            {"name": "Mode", "value": _get_mode().upper(), "inline": True},
        ],
    )

    payload = {"embeds": [embed]}
    return _send_webhook(url, payload)


# ============================================================================
# Signal Bridge Functions
# ============================================================================

def get_signal_file_path() -> str:
    """Get path to discord_signals.json"""
    return os.getenv("SIGNAL_FILE_PATH", "/opt/slimy/pm_updown_bot_bundle/discord_signals.json")


def read_signals() -> Dict[str, Any]:
    """Read the signal bridge file."""
    signal_file = get_signal_file_path()
    try:
        if os.path.exists(signal_file):
            with open(signal_file, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Failed to read signals: {e}")
    return {"pending_actions": [], "force_exits": [], "config_updates": []}


def write_signals(data: Dict[str, Any]) -> bool:
    """Write to the signal bridge file."""
    signal_file = get_signal_file_path()
    try:
        with open(signal_file, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to write signals: {e}")
        return False


def add_pending_action(action: Dict[str, Any]) -> bool:
    """Add a pending action to the signal file."""
    data = read_signals()
    action["timestamp"] = datetime.now(timezone.utc).isoformat()
    data["pending_actions"].append(action)
    return write_signals(data)


def add_force_exit(ticker: str) -> bool:
    """Add a force exit entry to the signal file."""
    data = read_signals()
    data["force_exits"].append({
        "ticker": ticker,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return write_signals(data)


def get_force_exits() -> List[Dict[str, Any]]:
    """Get and clear force exits."""
    data = read_signals()
    force_exits = data.get("force_exits", [])
    data["force_exits"] = []
    write_signals(data)
    return force_exits


def clear_pending_action(action_id: str) -> bool:
    """Clear a processed pending action."""
    data = read_signals()
    data["pending_actions"] = [
        a for a in data["pending_actions"] if a.get("id") != action_id
    ]
    return write_signals(data)


def export_config_for_nodejs() -> bool:
    """Export config to config.json for Node.js OpenClaw to read."""
    from config import PHASES, RISK_CAPS, VENUE_CONFIGS

    config_data = {
        "phases": PHASES,
        "risk_caps": RISK_CAPS,
        "venue_configs": VENUE_CONFIGS,
        "execution_mode": _get_mode(),
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }

    config_file = "/opt/slimy/pm_updown_bot_bundle/config.json"
    try:
        with open(config_file, 'w') as f:
            json.dump(config_data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to export config: {e}")
        return False
