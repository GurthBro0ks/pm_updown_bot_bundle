#!/usr/bin/env python3
"""
Central Configuration Module
All API keys, risk parameters, and phase settings in one place.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ============================================================================
# API Keys (loaded from environment)
# ============================================================================

# Finnhub - Stock news and sentiment
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

# Alpha Vantage - News sentiment (25 calls/day limit)
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")

# Massive (Polygon) - Stock prices
MASSIVE_API_KEY = os.getenv("MASSIVE_API_KEY")

# Kalshi - Election markets
KALSHI_KEY = os.getenv("KALSHI_KEY")
KALSHI_SECRET_FILE = os.getenv("KALSHI_SECRET_FILE", "./kalshi_private_key.pem")

# Polymarket (deprecated but kept for reference)
POLYMARKET_WALLET = os.getenv("POLYMARKET_WALLET")

# ============================================================================
# Phase Enable/Disable Flags
# ============================================================================

PHASES = {
    "phase1_kalshi": True,
    "phase2_sef": True,
    "phase3_stock_hunter": True,
    "phase4_airdrop": True,
    "phase5_ibkr": os.getenv("IBKR_ENABLED", "false").lower() == "true",
    "phase6_wallet_arb": True,  # Wallet tokens + arbitrage scanner
}

# ============================================================================
# Farming Configuration (Base Chain Airdrop)
# ============================================================================

FARMING_WEEKLY_BUDGET = float(os.getenv("FARMING_WEEKLY_BUDGET", "5.00"))
MIN_BASE_ETH_FOR_FARMING = float(os.getenv("MIN_BASE_ETH_FOR_FARMING", "0.002"))

# ============================================================================
# Risk Caps (unified across all phases)
# ============================================================================

RISK_CAPS = {
    # Global position limits
    "max_pos_usd": 10.0,
    "max_daily_loss_usd": 30.0,
    "max_open_pos": 3,
    "max_daily_positions": 10,
    "liquidity_min_usd": 0,
    "edge_after_fees_pct": 1.5,

    # Exit controls (mirrors rotation_config.json)
    "max_hold_hours": 168,  # Max 7 days per position

    # Phase 1: Kalshi Optimization
    "kalshi_maker_only": True,
    "kalshi_min_profit_usd": 0.10,
    "kalshi_max_daily_trades": 10,
    "maker_fee_usd": 0.00,
    "kalshi_fee_pct": 0.07,
    "kalshi_true_probability": 0.5,

    # Micro-live specific caps (penny trades, $0.50 daily loss limit)
    "micro_live_max_pos_usd": 0.01,
    "micro_live_max_daily_loss_usd": 0.50,
    "micro_live_max_open_positions": 5,
    "micro_live_max_daily_trades": 20,

    # Phase 2: SEF Spot Trading
    "sef_max_pos_usd": 20.0,
    "sef_max_daily_loss_usd": 20.0,
    "sef_max_open_pos": 3,
    "sef_max_daily_trades": 15,
    "sef_slippage_max_pct": 1.0,
    "sef_min_spread_pct": 0.5,
    "sef_gas_budget_usd": 10.0,

    # Phase 3: Stock Hunter
    "stock_max_pos_usd": 100.0,
    "stock_max_daily_loss_usd": 30.0,
    "stock_max_open_pos": 3,
    "stock_max_daily_positions": 10,
    "stock_min_liquidity_usd": 10000,
    "stock_sentiment_threshold": 0.55,
    "stock_min_market_cap_usd": 100_000_000,
    "stock_max_market_cap_usd": 300_000_000,
    "stock_min_price_usd": 1.0,
    "stock_max_price_usd": 5.0,

    # Weather Strategy (NOAA Arbitrage)
    "weather_max_daily_trades": 10,
    "weather_max_exposure_per_city": 1.0,
    "weather_min_edge_pct": 3.0,
    "weather_min_liquidity_usd": 50.0,
}

# ============================================================================
# Meme Tickers (apply sentiment discount)
# ============================================================================

MEME_TICKERS = {
    'GME', 'AMC', 'BBBY', 'PLTR', 'MARA', 'RIOT',
    'DWAC', 'SPCE', 'HOOD', 'DJT'
}

# ============================================================================
# Target Tickers (stock hunter)
# ============================================================================

STOCK_HUNTER_TICKERS = ["AAPL", "TSLA", "NVDA", "GME", "META"]

# ============================================================================
# SEF Trading Pairs
# ============================================================================

SEF_TRADING_PAIRS = [
    {"token_in": "WBTC", "token_out": "USDC"},
    {"token_in": "WETH", "token_out": "USDC"},
]

# ============================================================================
# Venue Configurations
# ============================================================================

VENUE_CONFIGS = {
    "kalshi": {
        "name": "Kalshi",
        "min_trade_usd": 0.01,
        "max_trade_usd": 10.0,
        "base_url": "https://api.elections.kalshi.com",
        "settlement": "USDC"
    },
    "polymarket": {
        "name": "Polymarket",
        "min_trade_usd": 0.01,
        "max_trade_usd": 100.0,
        "fee_pct": 0.005,
        "base_url": "https://api.thegraph.com/subgraphs/name/polymarket/markets",
        "settlement": "USDC",
        "wallet_address": POLYMARKET_WALLET,
        "shadow_mode": True
    },
    "ibkr": {
        "name": "Interactive Brokers",
        "min_trade_usd": 1.00,
        "max_trade_usd": 1000.0,
        "fee_pct": 0.01,
        "base_url": "mock://ibkr",
        "settlement": "USD"
    }
}

# ============================================================================
# Execution Modes
# ============================================================================

# Kelly fraction for position sizing
KELLY_FRAC_SHADOW = 0.25
KELLY_FRAC_LIVE = 0.05

# ============================================================================
# File Paths
# ============================================================================

BASE_DIR = Path("/opt/slimy/pm_updown_bot_bundle")
PROOF_DIR = BASE_DIR / "proofs"
LOGS_DIR = BASE_DIR / "logs"
PAPER_TRADING_DIR = BASE_DIR / "paper_trading"
CONFIG_DIR = BASE_DIR / "config"

PAPER_BALANCE_FILE = PAPER_TRADING_DIR / "paper_balance.json"
TRADE_LOG_FILE = PAPER_TRADING_DIR / "trade_log.json"
ROTATION_CONFIG_FILE = CONFIG_DIR / "rotation_config.json"

# ============================================================================
# Schedule Intervals (in seconds)
# ============================================================================

INTERVALS = {
    "phase1_interval": 3600,      # 1 hour
    "phase2_interval": 1800,      # 30 minutes
    "phase3_interval": 900,        # 15 minutes
    "phase4_interval": 7200,      # 2 hours
    "phase5_interval": 3600,      # 1 hour (IBKR ForecastTrader)
    "exit_check_interval": 300,    # 5 minutes
}

# ============================================================================
# Helper Functions
# ============================================================================

def is_phase_enabled(phase: str) -> bool:
    """Check if a phase is enabled"""
    return PHASES.get(phase, False)

def get_risk_cap(key: str, default=None):
    """Get a risk cap value"""
    return RISK_CAPS.get(key, default)

def get_venue_config(venue: str) -> dict:
    """Get venue configuration"""
    return VENUE_CONFIGS.get(venue, {})

# ============================================================================
# Initialization
# ============================================================================

# Ensure directories exist
for directory in [PROOF_DIR, LOGS_DIR, PAPER_TRADING_DIR, CONFIG_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# ─── Sentiment Scorer ─────────────────────────────────────────
SENTIMENT_PROVIDERS = ["grok_420", "grok_fast", "glm"]  # Grok primary, GLM fallback, MiniMax disabled (insufficient balance)
# PRODUCTION: ["grok_420", "grok_fast", "minimax", "glm"]
SENTIMENT_MAX_MARKETS = 50
SENTIMENT_CACHE_TTL = 600
