#!/usr/bin/env python3
"""
Multi-Venue Runner - Shipping Mode
Phase 1: Kalshi Optimization (Complete)
Phase 2: SEF Spot Trading (Complete)
Phase 3: Stock Hunter (In Progress)

Configuration centralized in config.py
"""

import argparse
import json
import logging
import os
import sys
import requests
import base64
import time
import random
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes

# Import centralized config
from config import (
    RISK_CAPS, VENUE_CONFIGS, PHASES, MEME_TICKERS,
    KELLY_FRAC_SHADOW, KELLY_FRAC_LIVE,
    PROOF_DIR, PAPER_BALANCE_FILE, TRADE_LOG_FILE,
    FINNHUB_API_KEY, is_phase_enabled, get_risk_cap
)

# Strategy imports
from strategies import kalshi_optimize as kalshi_opt_module
from strategies import sef_spot_trading as sef_opt_module
from strategies import stock_hunter as stock_hunter_module
from strategies import airdrop_farmer as airdrop_farmer_module
from strategies import weather_signals
from strategies import ibkr_forecast

# PnL Database
from utils.pnl_database import record_trade, snapshot_equity

# Rotation Manager (2026 Playbook)
try:
    from utils.rotation_manager import RotationManager
    ROTATION_MANAGER = RotationManager()
except:
    ROTATION_MANAGER = None

# Wallet Token Discovery (airdrop farming + holdings)
try:
    from utils.wallet_tokens import get_wallet_tokens, get_holdings_summary, enrich_with_prices
    WALLET_TOKENS_ENABLED = True
except ImportError as e:
    logger.warning(f"[WALLET] Could not import wallet_tokens: {e}")
    WALLET_TOKENS_ENABLED = False

# DEX Prices & Arbitrage
try:
    from utils.dex_prices import (
        get_dex_price,
        get_token_price_by_symbol, 
        get_cex_price, 
        check_arbitrage_opportunity,
        scan_for_arbitrage,
        get_token_pairs_dexscreener,
        POPULAR_TOKENS
    )
    DEX_PRICES_ENABLED = True
except ImportError as e:
    logger.warning(f"[DEX] Could not import dex_prices: {e}")
    DEX_PRICES_ENABLED = False


# ============================================================
# WALLET & ARBITRAGE FUNCTIONS
# ============================================================

def check_wallet_holdings(verbose: bool = True) -> dict:
    """
    Check wallet token holdings across chains.
    
    Returns:
        Summary dict with tokens and total value.
    """
    if not WALLET_TOKENS_ENABLED:
        return {"error": "Wallet tokens module not available"}
    
    try:
        summary = get_holdings_summary()
        if verbose:
            logger.info(f"[WALLET] Total holdings: ${summary.get('total_usd', 0):.2f}")
            for chain, data in summary.get("chains", {}).items():
                logger.info(f"[WALLET]   {chain}: ${data.get('total_usd', 0):.2f}")
        return summary
    except Exception as e:
        logger.error(f"[WALLET] Failed to get holdings: {e}")
        return {"error": str(e)}


def check_arbitrage_for_holdings(min_spread: float = 2.0) -> list:
    """
    Check arbitrage opportunities for tokens in wallet.
    
    Args:
        min_spread: Minimum spread percentage to report
    
    Returns:
        List of arbitrage opportunities.
    """
    if not DEX_PRICES_ENABLED or not WALLET_TOKENS_ENABLED:
        return []
    
    try:
        # Get wallet tokens
        summary = get_holdings_summary()
        
        # Extract token symbols from holdings
        token_symbols = []
        for chain, data in summary.get("chains", {}).items():
            for token in data.get("tokens", []):
                symbol = token.get("symbol", "").upper()
                if symbol and symbol not in token_symbols:
                    token_symbols.append(symbol)
        
        if not token_symbols:
            logger.info("[ARB] No tokens in wallet to check")
            return []
        
        # Check each for arbitrage
        opportunities = []
        for symbol in token_symbols:
            result = check_arbitrage_opportunity(symbol, min_spread)
            if result and result.arbitrage_opportunity:
                opportunities.append(result)
                logger.info(f"[ARB] 🎯 {symbol}: DEX ${result.dex_price:.4f} vs CEX ${result.cex_price:.4f} | Spread: {result.spread_percent:.2f}%")
        
        return opportunities
        
    except Exception as e:
        logger.error(f"[ARB] Failed to check arbitrage: {e}")
        return []


def scan_popular_arbitrage(min_spread: float = 2.0) -> list:
    """
    Scan popular tokens for arbitrage opportunities.
    
    Args:
        min_spread: Minimum spread percentage
    
    Returns:
        List of opportunities found.
    """
    if not DEX_PRICES_ENABLED:
        return []
    
    try:
        return scan_for_arbitrage(POPULAR_TOKENS, min_spread)
    except Exception as e:
        logger.error(f"[ARB] Scan failed: {e}")
        return []


# ============================================================
# MAIN TRADING FUNCTIONS
# ============================================================

def fetch_live_prices(tickers: list) -> dict:
    """Fetch current stock prices from Finnhub API.

    Args:
        tickers: List of stock ticker symbols (e.g., ['AAPL', 'TSLA'])

    Returns:
        Dict mapping ticker -> current price. Missing tickers excluded.
    """
    if not FINNHUB_API_KEY:
        logging.warning("[EXIT] FINNHUB_API_KEY not set, cannot fetch live prices")
        return {}

    prices = {}
    for ticker in tickers:
        try:
            url = f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_API_KEY}"
            resp = requests.get(url, timeout=5)
            data = resp.json()
            # Finnhub response: {"c": current, "h": high, "l": low, "o": open, "pc": prev_close, "t": timestamp}
            if data.get("c") and data["c"] > 0:
                prices[ticker] = data["c"]
                logging.debug(f"[EXIT] {ticker}: fetched ${data['c']:.2f}")
        except Exception as e:
            logging.warning(f"[EXIT] Failed to fetch {ticker}: {e}")
    return prices

# Paper Money Tracker (PAPER_BALANCE_FILE imported from config.py)

class PaperMoneyTracker:
    """Track virtual trades with $100 starting balance"""
    
    STARTING_BALANCE = 100.00
    
    def __init__(self):
        self.cash = self.STARTING_BALANCE
        self.positions = []  # [{"venue": "stock", "ticker": "AAPL", "shares": 0.1, "entry_price": 272.14, "size_usd": 10.0}]
        self.trades = []  # [{"time": "...", "action": "buy/sell", "ticker": "...", "shares": ..., "price": ..., "pnl": ...}]
        self.load()
    
    def load(self):
        """Load balance from file"""
        if PAPER_BALANCE_FILE.exists():
            try:
                data = json.loads(PAPER_BALANCE_FILE.read_text())
                self.cash = data.get("cash", self.STARTING_BALANCE)
                self.positions = data.get("positions", [])
                self.trades = data.get("trades", [])
            except:
                self.cash = self.STARTING_BALANCE
                self.positions = []
                self.trades = []
    
    def save(self):
        """Save balance to file"""
        data = {
            "cash": self.cash,
            "positions": self.positions,
            "trades": self.trades,
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
        PAPER_BALANCE_FILE.write_text(json.dumps(data, indent=2))
    
    def reset(self):
        """Reset to starting balance"""
        self.cash = self.STARTING_BALANCE
        self.positions = []
        self.trades = []
        self.save()
        logger.info(f"Paper balance RESET to ${self.cash:.2f}")
    
    def can_afford(self, size_usd):
        """Check if we can afford a trade"""
        return self.cash >= size_usd
    
    def execute_buy(self, venue, ticker, size_usd, price, sentiment=0.5):
        """Execute a virtual BUY"""
        if not self.can_afford(size_usd):
            logger.warning(f"[PAPER] Cannot afford {ticker}: need ${size_usd:.2f}, have ${self.cash:.2f}")
            return False
        
        shares = size_usd / price
        self.cash -= size_usd
        self.positions.append({
            "venue": venue,
            "ticker": ticker,
            "shares": shares,
            "entry_price": price,
            "size_usd": size_usd,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "sentiment": sentiment  # FIX: Save sentiment to position
        })
        self.trades.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "action": "buy",
            "venue": venue,
            "ticker": ticker,
            "shares": shares,
            "price": price,
            "size_usd": size_usd
        })
        self.save()
        # Record trade to SQLite database
        venue_to_phase = {"kalshi": "kalshi", "sef": "crypto", "stock": "stocks", "airdrop": "airdrop"}
        phase = venue_to_phase.get(venue, venue)
        try:
            record_trade(phase, ticker, "BUY", price, size_usd)
            logger.debug(f"[DB] Trade recorded: BUY {ticker} ${size_usd}")
        except Exception as e:
            logger.error(f"[DB] Failed to record BUY trade: {e}")
        logger.info(f"[PAPER] BUY {ticker}: {shares:.4f} @ ${price:.2f} = ${size_usd:.2f} | Cash: ${self.cash:.2f}")
        return True

    def execute_sell(self, venue, ticker, shares, price):
        """Execute a virtual SELL - calculates P&L"""
        # Find position
        pos = None
        for p in self.positions:
            if p["ticker"] == ticker and p["venue"] == venue:
                pos = p
                break
        
        if not pos:
            logger.warning(f"[PAPER] No position found for {ticker}")
            return False
        
        # Calculate P&L
        cost = pos["shares"] * pos["entry_price"]
        proceeds = shares * price
        pnl = proceeds - cost
        
        self.cash += proceeds
        self.positions = [p for p in self.positions if not (p["ticker"] == ticker and p["venue"] == venue)]
        
        self.trades.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "action": "sell",
            "venue": venue,
            "ticker": ticker,
            "shares": shares,
            "price": price,
            "size_usd": proceeds,
            "pnl": pnl,
            "cost_basis": cost
        })
        self.save()
        # Record trade to SQLite database
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0
        venue_to_phase = {"kalshi": "kalshi", "sef": "crypto", "stock": "stocks", "airdrop": "airdrop"}
        phase = venue_to_phase.get(venue, venue)
        record_trade(phase, ticker, "EXIT", price, proceeds, pnl_usd=pnl, pnl_pct=pnl_pct)
        logger.info(f"[PAPER] SELL {ticker}: {shares:.4f} @ ${price:.2f} = ${proceeds:.2f} | P&L: ${pnl:+.2f} | Cash: ${self.cash:.2f}")
        return True

    def get_current_value(self, prices=None):
        """Get current portfolio value. prices = {ticker: price}"""
        positions_value = 0.0
        if prices:
            for p in self.positions:
                current_price = prices.get(p["ticker"], p["entry_price"])
                positions_value += p["shares"] * current_price
        else:
            # Use entry prices if no current prices provided
            positions_value = sum(p["shares"] * p["entry_price"] for p in self.positions)
        return self.cash + positions_value
    
    def get_pnl(self, current_prices=None):
        """Calculate total P&L from starting balance"""
        current_value = self.get_current_value(current_prices)
        return current_value - self.STARTING_BALANCE
    
    def summary(self):
        """Get summary string"""
        # Load from file to get latest state
        self.load()
        pnl = self.get_pnl()
        return {
            "cash": self.cash,
            "positions": len(self.positions),
            "total_value": self.cash + sum(p["shares"] * p["entry_price"] for p in self.positions),
            "pnl": pnl,
            "starting": self.STARTING_BALANCE,
            "trades_today": len([t for t in self.trades if t["time"].startswith(datetime.now(timezone.utc).strftime("%Y-%m-%d"))])
        }
    
    def check_exits(self, rotation_manager=None, current_prices=None):
        """
        Check all positions for invalidation or profit-taking.
        Returns list of positions to exit.
        """
        if not rotation_manager:
            return []
        
        exits = []
        
        # Use entry prices if no current prices
        if not current_prices:
            current_prices = {p["ticker"]: p["entry_price"] for p in self.positions}
        
        for position in self.positions[:]:  # Copy list
            ticker = position.get("ticker")
            if not ticker:
                continue
            
            current_price = current_prices.get(ticker, position.get("entry_price"))
            position_with_price = position.copy()
            position_with_price["current_price"] = current_price
            
            # Check invalidation
            should_exit, reason = rotation_manager.check_invalidation(position_with_price)
            if should_exit:
                exits.append({
                    "ticker": ticker,
                    "reason": f"invalidation: {reason}",
                    "position": position
                })
                continue
            
            # Check profit taking
            should_take, pct = rotation_manager.check_profit_taking(position_with_price)
            if should_take:
                exits.append({
                    "ticker": ticker,
                    "reason": f"profit_take: {pct:.0%} at {((current_price/position['entry_price'])-1)*100:.0f}% gain",
                    "position": position,
                    "pct_to_take": pct
                })
        
        return exits

# Global tracker instance
paper_money = PaperMoneyTracker()

# Trade Log Functions (for entries, checks, exits)
def log_trade_entry(entry_data: dict):
    """Log a trade entry to trade_log.json"""
    try:
        if TRADE_LOG_FILE.exists():
            with open(TRADE_LOG_FILE, 'r') as f:
                data = json.load(f)
        else:
            data = {"entries": [], "checks": [], "exits": [], "metadata": {}}

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **entry_data
        }
        data["entries"].append(entry)

        with open(TRADE_LOG_FILE, 'w') as f:
            json.dump(data, f, indent=2)

        logger.debug(f"Logged trade entry: {entry_data.get('ticker', 'unknown')}")
    except Exception as e:
        logger.warning(f"Failed to log trade entry: {e}")


def log_trade_check(check_data: dict):
    """Log a position check to trade_log.json"""
    try:
        if TRADE_LOG_FILE.exists():
            with open(TRADE_LOG_FILE, 'r') as f:
                data = json.load(f)
        else:
            data = {"entries": [], "checks": [], "exits": [], "metadata": {}}

        check = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **check_data
        }
        data["checks"].append(check)

        with open(TRADE_LOG_FILE, 'w') as f:
            json.dump(data, f, indent=2)

        logger.debug(f"Logged position check: {check_data.get('ticker', 'unknown')}")
    except Exception as e:
        logger.warning(f"Failed to log trade check: {e}")


def log_trade_exit(exit_data: dict):
    """Log a trade exit to trade_log.json"""
    try:
        if TRADE_LOG_FILE.exists():
            with open(TRADE_LOG_FILE, 'r') as f:
                data = json.load(f)
        else:
            data = {"entries": [], "checks": [], "exits": [], "metadata": {}}

        exit_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **exit_data
        }
        data["exits"].append(exit_entry)

        with open(TRADE_LOG_FILE, 'w') as f:
            json.dump(data, f, indent=2)

        logger.debug(f"Logged trade exit: {exit_data.get('ticker', 'unknown')}")
    except Exception as e:
        logger.warning(f"Failed to log trade exit: {e}")


load_dotenv()

# Setup logging with valid format (no syntax error)
log_format = '%(asctime)s | %(levelname)s | %(message)s'
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    handlers=[
        logging.FileHandler('/opt/slimy/pm_updown_bot_bundle/logs/runner.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

CURRENT_PHASE = "shipping_mode"  # Phase 1 & 2 complete, Phase 3 in progress

# RISK_CAPS now imported from config.py
# VENUE_CONFIGS now imported from config.py

secret_file = os.getenv('KALSHI_SECRET_FILE', './kalshi_private_key.pem')
with open(secret_file, 'rb') as f:
    private_key = serialization.load_pem_private_key(f.read(), password=None)

def get_headers(method, path):
    timestamp = str(int(time.time()))
    base_path = path.split('?')[0]
    to_sign = f"{timestamp}\n{method}\n{base_path}"
    signature = private_key.sign(to_sign.encode('ascii'), padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.b64encode(signature).decode('ascii')
    auth_header = f'RSA keyId="{os.getenv("KALSHI_KEY")}",timestamp="{timestamp}",signature="{sig_b64}"'
    return {'Authorization': auth_header}

def generate_proof(proof_id, data):
    proof_path = PROOF_DIR / f"{proof_id}.json"
    with open(proof_path, 'w') as f:
        json.dump(data, f, indent=2)
    logger.info(f"Proof: {proof_path}")

def fetch_kalshi_markets():
    api_key = os.getenv("KALSHI_KEY")
    if not api_key:
        logger.warning("WARNING: No KALSHI_KEY - using mock")
        return [
            {"id": "FED-25.FEB", "question": "Fed rate", "odds": {"yes": 0.72, "no": 0.28}, "liquidity_usd": 25000, "hours_to_end": 720, "fees_pct": 0.01},
            {"id": "CPI-25.FEB", "question": "CPI", "odds": {"yes": 0.55, "no": 0.45}, "liquidity_usd": 50000, "hours_to_end": 48, "fees_pct": 0.01}
        ]
    headers = get_headers('GET', '/v1/markets')
    resp = requests.get('https://api.elections.kalshi.com/trade-api/v2/markets', headers=headers, params={'status': 'open', 'limit': 100}, timeout=10)
    if resp.status_code == 200:
        data = resp.json() if resp.text.strip() else {"markets": []}
        markets = []
        for m in data.get('markets', []):
            ticker = m.get('ticker', '')
            yes_bid_cents = m.get('yes_bid', 0)
            yes_ask_cents = m.get('yes_ask', 0)
            if yes_ask_cents <= 0:
                continue
            yes_bid_cents = m.get('yes_bid', 0)
            yes_ask_cents = m.get('yes_ask', 0)
            yes_price_cents = (yes_bid_cents + yes_ask_cents) / 2
            yes_price = yes_price_cents / 100.0
            no_price = 1.0 - yes_price
            liquidity_usd = m.get('open_interest', 0) * yes_price
            markets.append({
                "id": ticker,
                "question": m.get('short_name', ticker),
                "odds": {"yes": yes_price, "no": no_price},
                "liquidity_usd": liquidity_usd,
                "hours_to_end": 48
            })
        logger.info(f"Fetched {len(markets)} markets")
        return markets
    logger.error("API fail - using mock")
    return []

def run_phase1_kalshi_optimization(mode, bankroll, max_pos_usd):
    """Phase 1: Kalshi Optimization (Complete)"""
    logger.info("=" * 60)
    logger.info("PHASE 1: KALSHI OPTIMIZATION (COMPLETE)")
    logger.info(f"Mode: {mode}")
    logger.info(f"Bankroll: ${bankroll:.2f}")
    logger.info(f"Max position: ${max_pos_usd:.2f}")
    logger.info("=" * 60)
    
    try:
        if not hasattr(kalshi_opt_module, 'optimize_kalshi_strategy'):
            logger.error("Kalshi optimization module not found")
            return 0
        
        result = kalshi_opt_module.optimize_kalshi_strategy(
            mode=mode,
            bankroll=bankroll,
            max_pos_usd=max_pos_usd,
            dry_run=(mode == "shadow")
        )
        
        logger.info(f"Phase 1 optimization complete - result: {result}")
        return 1
    except Exception as e:
        logger.error(f"Phase 1 error: {str(e)}")
        return 0

def normalize_trading_pairs(trading_pairs):
    """
    Normalize trading pairs to list of dicts format expected by SEF module
    
    Args:
        trading_pairs: Could be list of tokens or list of token pair dicts
    
    Returns:
        List of token pair dicts: [{"token_in": "WBTC", "token_out": "USDC"}, ...]
    """
    normalized = []
    
    if not trading_pairs:
        return []
    
    for item in trading_pairs:
        # If already a dict with correct format
        if isinstance(item, dict) and "token_in" in item and "token_out" in item:
            normalized.append(item)
            continue
        
        # If it's just a token string, convert to pair
        if isinstance(item, str):
            # Assume token -> USDC for spot trading
            token_in = item
            token_out = "USDC"
            normalized.append({"token_in": token_in, "token_out": token_out})
            continue
    
    logger.debug(f"Normalized {len(normalized)} trading pairs")
    return normalized


def run_phase2_sef_spot_trading(mode, bankroll, max_pos_usd):
    """Phase 2: SEF Spot Trading (Complete)"""
    logger.info("=" * 60)
    logger.info("PHASE 2: SEF SPOT TRADING (COMPLETE)")
    logger.info(f"Mode: {mode}")
    logger.info(f"Bankroll: ${bankroll:.2f}")
    logger.info(f"Max position: ${max_pos_usd:.2f}")
    logger.info("=" * 60)
    
    # Normalize trading pairs if needed
    # trading_pairs = [  # TODO: Configure which pairs to trade
    # ]
    # normalized = normalize_trading_pairs(trading_pairs)
    
    try:
        if not hasattr(sef_opt_module, 'main'):
            logger.error("SEF spot trading module not found")
            return 0
        
        # Check if module has trading_pairs config
        if hasattr(sef_opt_module, 'trading_pairs'):
            pairs = sef_opt_module.trading_pairs
        else:
            # Default to WBTC-USDC if no config
            pairs = [{"token_in": "WBTC", "token_out": "USDC"}]
        
        result = sef_opt_module.main(mode=mode, bankroll=bankroll, max_pos_usd=max_pos_usd)
        
        logger.info(f"Phase 2 complete - result: {result}")
        return 1
    except Exception as e:
        logger.error(f"Phase 2 error: {str(e)}")
        return 0

def run_phase3_stock_hunter(mode, bankroll, max_pos_usd):
    """Phase 3: Stock Hunter (In Progress)"""
    logger.info("=" * 60)
    logger.info("PHASE 3: STOCK HUNTER (IN PROGRESS)")
    logger.info(f"Mode: {mode}")
    logger.info(f"Bankroll: ${bankroll:.2f}")
    logger.info(f"Max position: ${max_pos_usd:.2f}")

    # FIX: Check for exits EVERY cycle before checking new entries
    # This ensures positions held too long are recycled
    if paper_money.positions and ROTATION_MANAGER:
        stock_tickers = [p["ticker"] for p in paper_money.positions if p.get("venue") == "stock"]
        if stock_tickers:
            logger.info("[EXIT CHECK] Fetching live prices for exit check...")
            current_prices = fetch_live_prices(stock_tickers)
            
            # Fallback to entry price if live fetch fails
            for p in paper_money.positions:
                if p["ticker"] not in current_prices:
                    current_prices[p["ticker"]] = p["entry_price"]
            
            # Check for exits (includes max_hold_hours check from rotation_config.json)
            exits = paper_money.check_exits(ROTATION_MANAGER, current_prices)
            
            if exits:
                logger.warning(f"[EXIT CHECK] {len(exits)} positions to exit:")
                for exit_info in exits:
                    logger.warning(f"  - {exit_info['ticker']}: {exit_info['reason']}")
                    position = exit_info["position"]
                    sell_price = current_prices.get(position["ticker"], position["entry_price"])
                    paper_money.execute_sell(
                        position["venue"],
                        position["ticker"],
                        position["shares"],
                        sell_price
                    )
                logger.info("[EXIT CHECK] Exits completed, rechecking positions...")
            else:
                logger.info("[EXIT CHECK] No exits triggered")

    # BUG 4 FIX: Enforce max positions (count only STOCK positions, not kalshi/SEF)
    stock_positions = [p for p in paper_money.positions if p.get("venue") == "stock"]
    current_positions = len(stock_positions)
    max_positions = RISK_CAPS.get("stock_max_open_pos", 3)
    logger.info(f"Stock Positions: {current_positions}/{max_positions}")

    if current_positions >= max_positions:
        logger.info(f"[ENTRY] Skipping — max positions ({max_positions}) reached. Current: {current_positions}")
        return 1 # Success - max positions reached, no action needed

    # Fetch weather-driven commodity signals (MONITOR ONLY - don't auto-trade)
    logger.info("Checking weather-driven commodity signals...")
    weather_signals_list = weather_signals.fetch_weather_signals()
    for sig in weather_signals_list:
        logger.info(f"[WEATHER→TRADE] Consider {sig['direction']} {sig['etf']}: "
                    f"{sig['trigger']} ({sig['strength']:.0%} confidence, region={sig['region']})")

    logger.info("=" * 60)

    try:
        if not hasattr(stock_hunter_module, 'main'):
            logger.error("Stock hunter module not found")
            return 0

        # FIXED: Pass RISK_CAPS to stock_hunter so it uses the same thresholds
        result = stock_hunter_module.main(
            mode=mode,
            bankroll=bankroll,
            max_pos_usd=max_pos_usd,
            risk_caps=RISK_CAPS  # Sync thresholds between runner and stock_hunter
        )

        logger.info(f"Phase 3 stock hunter complete - result: {result}")
        return 1
    except Exception as e:
        logger.error(f"Phase 3 error: {str(e)}")
        return 0

def run_phase4_airdrop_farming(mode):
    """Phase 4: Airdrop Farming Automation (TRACKER MODE)"""
    logger.info("=" * 60)
    logger.info("PHASE 4: AIRDROP FARMING (TRACKER MODE)")
    logger.info(f"Mode: {mode}")
    logger.info("=" * 60)

    try:
        if not hasattr(airdrop_farmer_module, 'main'):
            logger.error("Airdrop farmer module not found")
            return 0

        result = airdrop_farmer_module.main(mode=mode)

        logger.info(f"Phase 4 complete - result: {result}")
        return 1
    except Exception as e:
        logger.error(f"Phase 4 error: {str(e)}")
        return 0


def run_phase5_ibkr_forecast(mode):
    """Phase 5: IBKR ForecastTrader (DISABLED by default)"""
    logger.info("=" * 60)
    logger.info("PHASE 5: IBKR FORECASTTRADER")
    logger.info(f"Mode: {mode}")
    logger.info("=" * 60)

    try:
        result = ibkr_forecast.run_ibkr_forecast(mode=mode)

        logger.info(f"Phase 5 complete - result: {result}")
        return 1 if result else 0
    except Exception as e:
        logger.error(f"Phase 5 error: {str(e)}")
        return 0


def run_phase6_wallet_arbitrage(mode, min_spread: float = 2.0):
    """Phase 6: Wallet Tokens + Arbitrage Scanner"""
    logger.info("=" * 60)
    logger.info("PHASE 6: WALLET TOKENS + ARBITRAGE")
    logger.info(f"Mode: {mode}")
    logger.info(f"Min spread: {min_spread}%")
    logger.info("=" * 60)

    results = {"holdings": None, "arbitrage": []}

    # 1. Check wallet holdings
    if WALLET_TOKENS_ENABLED:
        try:
            holdings = check_wallet_holdings(verbose=True)
            results["holdings"] = holdings
        except Exception as e:
            logger.error(f"[WALLET] Error: {e}")

    # 2. Scan for arbitrage on held tokens
    if DEX_PRICES_ENABLED and WALLET_TOKENS_ENABLED:
        try:
            opportunities = check_arbitrage_for_holdings(min_spread=min_spread)
            results["arbitrage"] = [
                {
                    "token": opp.token,
                    "dex_price": opp.dex_price,
                    "cex_price": opp.cex_price,
                    "spread": opp.spread_percent,
                    "profit": opp.profit_margin
                }
                for opp in opportunities
            ]
            if opportunities:
                logger.info(f"[ARB] Found {len(opportunities)} opportunity(ies)")
        except Exception as e:
            logger.error(f"[ARB] Error: {e}")

    # 3. Also scan popular tokens if no wallet holdings
    if DEX_PRICES_ENABLED and not results["arbitrage"]:
        try:
            popular_opps = scan_popular_arbitrage(min_spread=min_spread)
            results["arbitrage"] = [
                {
                    "token": opp.token,
                    "dex_price": opp.dex_price,
                    "cex_price": opp.cex_price,
                    "spread": opp.spread_percent,
                    "profit": opp.profit_margin
                }
                for opp in popular_opps
            ]
            if popular_opps:
                logger.info(f"[ARB] Found {len(popular_opps)} popular token opportunity(ies)")
        except Exception as e:
            logger.debug(f"[ARB] Popular scan error: {e}")

    logger.info(f"Phase 6 complete")
    return 1


def main():
    parser = argparse.ArgumentParser(description="Multi-Venue Runner - Shipping Mode")
    parser.add_argument("--mode", choices=["shadow", "micro-live", "real-live"], default="shadow", help="Execution mode (micro-live = real trades with risk gates)")
    parser.add_argument("--phase", choices=["phase1", "phase2", "phase3", "phase4", "phase5", "phase6", "all"], default="all", help="Phase to execute")
    parser.add_argument("--bankroll", type=float, default=100.0, help="Bankroll in USD")
    parser.add_argument("--max-pos", type=float, default=10.0, help="Max position size in USD")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    logger.info("=" * 60)
    logger.info(f"MODE: {args.mode.upper()}")
    logger.info(f"PHASE: {args.phase.upper()}")
    logger.info(f"BANKROLL: ${args.bankroll:.2f}")
    logger.info(f"MAX POS: ${args.max_pos:.2f}")
    logger.info("=" * 60)

    # Check for Discord force exits (from OpenClaw bot)
    try:
        from utils.discord_alerts import get_force_exits
        force_exits = get_force_exits()
        if force_exits:
            logger.warning(f"[DISCORD] Force exits requested: {[e['ticker'] for e in force_exits]}")
            # Execute force exits
            for exit_req in force_exits:
                ticker = exit_req.get("ticker")
                if ticker:
                    # Find and close position
                    for p in paper_money.positions[:]:
                        if p["ticker"] == ticker:
                            current_prices = fetch_live_prices([ticker])
                            sell_price = current_prices.get(ticker, p["entry_price"])
                            paper_money.execute_sell(p["venue"], ticker, p["shares"], sell_price)
                            logger.warning(f"[DISCORD] Force exit executed: {ticker} @ ${sell_price:.2f}")
    except Exception as e:
        logger.debug(f"[DISCORD] Signal bridge not available: {e}")

    # Check rotation manager for exits (shadow mode only)
    if args.mode == "shadow" and ROTATION_MANAGER:
        # Get rotation recommendation
        logger.info(f"[ROTATION] {ROTATION_MANAGER.get_rotation()}: {ROTATION_MANAGER.get_recommendation().split(chr(10))[0]}")

        # Check for exits using LIVE prices (Bug 1 fix)
        if paper_money.positions:
            position_tickers = [p["ticker"] for p in paper_money.positions]
            current_prices = fetch_live_prices(position_tickers)

            # Fallback: if price fetch fails, use entry_price (safe - won't trigger false exits)
            for p in paper_money.positions:
                if p["ticker"] not in current_prices:
                    current_prices[p["ticker"]] = p["entry_price"]
                    logger.warning(f"[EXIT] Using entry price for {p['ticker']} (live fetch failed)")

            # Log PnL for each position
            for p in paper_money.positions:
                ticker = p["ticker"]
                entry = p["entry_price"]
                current = current_prices.get(ticker, entry)
                pnl_pct = (current - entry) / entry * 100
                logger.info(f"[EXIT] {ticker}: entry=${entry:.2f} current=${current:.2f} PnL={pnl_pct:+.1f}%")

            exits = paper_money.check_exits(ROTATION_MANAGER, current_prices)
        else:
            exits = []

        if exits:
            logger.warning(f"[ROTATION] {len(exits)} positions to exit:")
            for exit_info in exits:
                logger.warning(f"  - {exit_info['ticker']}: {exit_info['reason']}")
                # Execute exit
                position = exit_info["position"]
                pct = exit_info.get("pct_to_take", 1.0)

                # Calculate shares to sell
                shares_to_sell = position["shares"] * pct
                if shares_to_sell > 0:
                    # Use live price if available, otherwise entry price
                    sell_price = current_prices.get(position["ticker"], position["entry_price"])
                    paper_money.execute_sell(
                        position["venue"],
                        position["ticker"],
                        shares_to_sell,
                        sell_price
                    )

    results = {}

    # Phase enable/disable checks (from config.py)
    phase1_enabled = is_phase_enabled("phase1_kalshi")
    phase2_enabled = is_phase_enabled("phase2_sef")
    phase3_enabled = is_phase_enabled("phase3_stock_hunter")
    phase4_enabled = is_phase_enabled("phase4_airdrop")
    phase5_enabled = is_phase_enabled("phase5_ibkr")
    phase6_enabled = is_phase_enabled("phase6_wallet_arb")  # New phase

    if args.phase == "phase1" or args.phase == "all":
        if not phase1_enabled:
            logger.info("Phase 1 (Kalshi) disabled in config - skipping")
            results["phase1"] = 0
        else:
            logger.info("Starting Phase 1: Kalshi Optimization")
            result_phase1 = run_phase1_kalshi_optimization(
                mode=args.mode,
                bankroll=args.bankroll,
                max_pos_usd=args.max_pos
            )
            results["phase1"] = result_phase1
    else:
        results["phase1"] = 0

    if args.phase == "phase2" or args.phase == "all":
        if not phase2_enabled:
            logger.info("Phase 2 (SEF) disabled in config - skipping")
            results["phase2"] = 0
        else:
            logger.info("Starting Phase 2: SEF Spot Trading")
            result_phase2 = run_phase2_sef_spot_trading(
                mode=args.mode,
                bankroll=args.bankroll,
                max_pos_usd=args.max_pos
            )
            results["phase2"] = result_phase2
    else:
        results["phase2"] = 0

    if args.phase == "phase3" or args.phase == "all":
        if not phase3_enabled:
            logger.info("Phase 3 (Stock Hunter) disabled in config - skipping")
            results["phase3"] = 0
        else:
            logger.info("Starting Phase 3: Stock Hunter")
            result_phase3 = run_phase3_stock_hunter(
                mode=args.mode,
                bankroll=args.bankroll,
                max_pos_usd=args.max_pos
            )
            results["phase3"] = result_phase3
    else:
        results["phase3"] = 0

    if args.phase == "phase4" or args.phase == "all":
        if not phase4_enabled:
            logger.info("Phase 4 (Airdrop) disabled in config - skipping")
            results["phase4"] = 0
        else:
            logger.info("Starting Phase 4: Airdrop Farming")
            result_phase4 = run_phase4_airdrop_farming(
                mode=args.mode
            )
            results["phase4"] = result_phase4

            # Show airdrop todo after phase 4 completes
            try:
                from utils.airdrop_tracker import get_weekly_todo
                todos = get_weekly_todo()
                if todos:
                    logger.info("=" * 60)
                    logger.info("AIRDROP TODO - Next Actions Recommended")
                    logger.info("=" * 60)
                    for todo in todos[:5]:  # Show top 5
                        logger.info(f"  [{todo['priority'].upper()}] {todo['name']}: {todo['action']}")
                    logger.info("=" * 60)
            except Exception as e:
                logger.debug(f"Airdrop todo not available: {e}")

            # Snapshot wallet balances after phase 4
            try:
                from utils.wallet_tracker import snapshot_balances
                wallet_snapped = snapshot_balances()
                if wallet_snapped:
                    logger.info("Wallet balance snapshot recorded")
            except Exception as e:
                logger.debug(f"Wallet snapshot not available: {e}")
    else:
        results["phase4"] = 0

    if args.phase == "phase5" or args.phase == "all":
        if not phase5_enabled:
            logger.info("Phase 5 (IBKR ForecastTrader) disabled in config - skipping")
            results["phase5"] = 0
        else:
            logger.info("Starting Phase 5: IBKR ForecastTrader")
            result_phase5 = run_phase5_ibkr_forecast(
                mode=args.mode
            )
            results["phase5"] = result_phase5
    else:
        results["phase5"] = 0

    # Phase 6: Wallet Tokens + Arbitrage
    if args.phase == "phase6" or args.phase == "all":
        if not phase6_enabled:
            logger.info("Phase 6 (Wallet+Arbitrage) disabled in config - skipping")
            results["phase6"] = 0
        else:
            logger.info("Starting Phase 6: Wallet Tokens + Arbitrage")
            result_phase6 = run_phase6_wallet_arbitrage(
                mode=args.mode,
                min_spread=2.0
            )
            results["phase6"] = result_phase6
    else:
        results["phase6"] = 0

    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Phase 1 (Kalshi): {'Success' if results['phase1'] else 'Failed'}")
    logger.info(f"Phase 2 (SEF): {'Success' if results['phase2'] else 'Failed'}")
    logger.info(f"Phase 3 (Stock Hunter): {'Success' if results['phase3'] else 'Failed'}")
    logger.info(f"Phase 4 (Airdrop Farming): {'Success' if results['phase4'] else 'Failed'}")
    logger.info(f"Phase 5 (IBKR ForecastTrader): {'Success' if results['phase5'] else 'Failed'}")
    logger.info(f"Phase 6 (Wallet+Arbitrage): {'Success' if results.get('phase6', 0) else 'Failed'}")
    logger.info("=" * 60)
    
    proof_id = f"shipping_mode_{args.phase}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    proof_data = {
        "mode": args.mode,
        "phase": args.phase,
        "bankroll": args.bankroll,
        "max_pos_usd": args.max_pos,
        "results": results,
        "risk_caps": RISK_CAPS
    }
    
    generate_proof(proof_id, proof_data)
    logger.info(f"Proof: {proof_id}")
    
    # Paper money summary (always shown)
    if args.mode == "shadow":
        paper_money.load()  # Reload to get latest trades from sub-modules
        summary = paper_money.summary()
        logger.info("=" * 60)
        logger.info("PAPER MONEY TRACKER")
        logger.info("=" * 60)
        logger.info(f"Starting: ${summary['starting']:.2f}")
        logger.info(f"Cash: ${summary['cash']:.2f}")
        logger.info(f"Positions: {summary['positions']}")
        logger.info(f"Total Value: ${summary['total_value']:.2f}")
        logger.info(f"P&L: ${summary['pnl']:+.2f}")
        logger.info(f"Trades Today: {summary['trades_today']}")
        logger.info("=" * 60)
        # Record equity snapshot to SQLite database
        snapshot_equity(summary['cash'], summary['total_value'] - summary['cash'], summary['positions'])

    exit_code = 0 if (results.get('phase1', 0) or results.get('phase2', 0) or results.get('phase3', 0)) else 1
    
    logger.info(f"Exit code: {exit_code}")
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
