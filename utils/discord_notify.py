"""
Discord webhook notifications for SlimyAI trading bot.
Sends alerts for: order placed, order won/filled, order lost, daily report.
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
DISCORD_TRADE_WEBHOOK_URL = os.environ.get("DISCORD_TRADE_WEBHOOK_URL", DISCORD_WEBHOOK_URL)

# Color codes for Discord embeds (decimal, not hex)
COLOR_ORDER_PLACED = 3447003   # Blue
COLOR_ORDER_WON    = 3066993   # Green
COLOR_ORDER_LOST   = 15158332  # Red
COLOR_DAILY_REPORT = 15844367  # Gold
COLOR_ERROR        = 10038562  # Dark red


def _send_embed(title: str, description: str, color: int, fields: list = None,
                footer: str = None, webhook_url: str = None) -> bool:
    """Send a Discord embed message via webhook. Returns True on success."""
    url = webhook_url or DISCORD_WEBHOOK_URL
    if not url:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping notification")
        return False

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if fields:
        embed["fields"] = fields
    if footer:
        embed["footer"] = {"text": footer}

    payload = {"embeds": [embed]}

    try:
        resp = requests.post(
            url,
            json=payload,
            timeout=10,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 204:
            logger.info(f"Discord notification sent: {title}")
            return True
        else:
            logger.error(f"Discord webhook returned {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"Discord notification failed: {e}")
        return False


def notify_order_placed(ticker: str, side: str, price_cents: float,
                        quantity: int, ai_probability: float = None,
                        edge_pct: float = None, kelly_fraction: float = None,
                        cascade_provider: str = None,
                        days_to_expiry: float = None):
    """Notify Discord that a new order was placed."""
    fields = [
        {"name": "Ticker", "value": f"`{ticker}`", "inline": True},
        {"name": "Side", "value": side.upper(), "inline": True},
        {"name": "Price", "value": f"{price_cents}¢", "inline": True},
        {"name": "Qty", "value": str(quantity), "inline": True},
    ]
    if ai_probability is not None:
        fields.append({"name": "AI Prob", "value": f"{ai_probability:.1%}", "inline": True})
    if edge_pct is not None:
        fields.append({"name": "Edge", "value": f"{edge_pct:.1f}%", "inline": True})
    if kelly_fraction is not None:
        fields.append({"name": "Kelly", "value": f"{kelly_fraction:.3f}", "inline": True})
    if cascade_provider:
        fields.append({"name": "Model", "value": cascade_provider, "inline": True})
    if days_to_expiry is not None:
        fields.append({"name": "Expiry", "value": f"{days_to_expiry:.0f}d", "inline": True})

    _send_embed(
        title="📋 Order Placed",
        description=f"**{side.upper()}** `{ticker}` @ {price_cents}¢",
        color=COLOR_ORDER_PLACED,
        fields=fields,
        webhook_url=DISCORD_TRADE_WEBHOOK_URL,
    )


def notify_order_won(ticker: str, side: str, price_cents: float,
                     pnl_usd: float = None, settled_price: float = None):
    """Notify Discord that an order settled in our favor."""
    desc = f"**{side.upper()}** `{ticker}` @ {price_cents}¢"
    fields = []
    if pnl_usd is not None:
        fields.append({"name": "PNL", "value": f"${pnl_usd:+.2f}", "inline": True})
    if settled_price is not None:
        fields.append({"name": "Settled At", "value": f"{settled_price}¢", "inline": True})

    _send_embed(
        title="✅ Order Won",
        description=desc,
        color=COLOR_ORDER_WON,
        fields=fields,
        footer="💰 Ka-ching!",
        webhook_url=DISCORD_TRADE_WEBHOOK_URL,
    )


def notify_order_lost(ticker: str, side: str, price_cents: float,
                      pnl_usd: float = None, settled_price: float = None):
    """Notify Discord that an order settled against us."""
    desc = f"**{side.upper()}** `{ticker}` @ {price_cents}¢"
    fields = []
    if pnl_usd is not None:
        fields.append({"name": "PNL", "value": f"${pnl_usd:+.2f}", "inline": True})
    if settled_price is not None:
        fields.append({"name": "Settled At", "value": f"{settled_price}¢", "inline": True})

    _send_embed(
        title="❌ Order Lost",
        description=desc,
        color=COLOR_ORDER_LOST,
        fields=fields,
        webhook_url=DISCORD_TRADE_WEBHOOK_URL,
    )


def notify_error(error_msg: str, context: str = ""):
    """Notify Discord of a critical error."""
    _send_embed(
        title="🚨 Bot Error",
        description=f"`{error_msg[:1500]}`",
        color=COLOR_ERROR,
        footer=context[:200] if context else None,
        webhook_url=DISCORD_TRADE_WEBHOOK_URL,
    )


def send_daily_report(balance_usd: float, open_positions: list,
                      trades_24h: list = None, trades_7d_summary: dict = None):
    """
    Send the daily summary report.
    Called by cron at 05:00 America/Detroit.
    """
    fields = [
        {"name": "💵 Balance", "value": f"${balance_usd:.2f}", "inline": True},
        {"name": "📊 Open Positions", "value": str(len(open_positions)), "inline": True},
    ]

    if trades_24h is not None:
        fields.append({
            "name": "🕐 Last 24h",
            "value": f"{len(trades_24h)} orders placed",
            "inline": True,
        })

    if trades_7d_summary:
        total_orders = trades_7d_summary.get("total_orders", 0)
        total_fills = trades_7d_summary.get("total_fills", 0)
        total_pnl = trades_7d_summary.get("total_pnl_usd", 0)
        fill_rate = (total_fills / total_orders * 100) if total_orders > 0 else 0
        fields.extend([
            {"name": "📈 7d Orders", "value": str(total_orders), "inline": True},
            {"name": "🎯 7d Fill Rate", "value": f"{fill_rate:.1f}%", "inline": True},
            {"name": "💰 7d PNL", "value": f"${total_pnl:+.2f}", "inline": True},
        ])

    # Top open positions detail
    pos_lines = []
    for p in open_positions[:8]:
        ticker = p.get("ticker", p.get("market_ticker", "???"))
        side = p.get("side", "?")
        qty = p.get("quantity", p.get("count", "?"))
        pos_lines.append(f"`{ticker}` {side} x{qty}")

    if pos_lines:
        fields.append({
            "name": "📋 Positions",
            "value": "\n".join(pos_lines),
            "inline": False,
        })

    _send_embed(
        title="📊 SlimyAI Daily Report",
        description=f"Report generated at {datetime.now().strftime('%Y-%m-%d %H:%M %Z')}",
        color=COLOR_DAILY_REPORT,
        fields=fields,
        footer="SlimyAI Prediction Market Bot | NUC1",
    )
