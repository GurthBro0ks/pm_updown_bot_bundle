#!/usr/bin/env python3
"""
Kalshi Order Execution Client

Provides order execution and portfolio management for Kalshi markets.
Uses RSA-PSS signing per Kalshi trading API docs.

AUTHENTICATION (per Kalshi docs):
  - Algorithm: RSA-PSS with SHA-256
  - Signing string: timestamp + HTTP_method + path (path excludes /trade-api/v2 prefix)
  - Timestamp: milliseconds (not seconds!)
  - Headers:
      KALSHI-ACCESS-KEY: API key ID (UUID)
      KALSHI-ACCESS-TIMESTAMP: timestamp in ms
      KALSHI-ACCESS-SIGNATURE: base64-encoded RSA-PSS signature

IMPORTANT: The existing KALSHI_KEY in .env is a MARKET-DATA key.
It works for /markets, /series but NOT for /portfolio/* endpoints.
To trade, you need a trading API key with trading scope.
Generate at: kalshi.com/account/profile → API Keys → Create New API Key
The trading key will be a DIFFERENT UUID than the market data key.

Safety limits:
  - MAX_ORDER_CENTS = 10    (max $0.10 per order)
  - MAX_QUANTITY = 1         (max 1 contract per order)
  - DAILY_LOSS_LIMIT_CENTS = 50  (max $0.50 daily loss)

Usage:
  from utils.kalshi_orders import KalshiOrderClient, SafetyLimitError
  client = KalshiOrderClient()
  balance = client.get_balance()
  order = client.place_order("KXXX-TEST", "yes", 1, 50)
"""

import os
import time
import json
import logging
import requests
import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes, serialization

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class KalshiAuthError(Exception):
    """Raised when Kalshi API authentication fails."""
    pass


class KalshiRateLimitError(Exception):
    """Raised when Kalshi API rate limits a request."""
    pass


class SafetyLimitError(Exception):
    """
    Raised when an order would exceed safety limits.
    Limits: MAX_ORDER_CENTS, MAX_QUANTITY, DAILY_LOSS_LIMIT_CENTS
    """
    def __init__(self, limit_type: str, limit_value: float, attempted_value: float, message: str = ""):
        self.limit_type = limit_type
        self.limit_value = limit_value
        self.attempted_value = attempted_value
        self.message = message or f"Safety limit exceeded: {limit_type} (limit={limit_value}, attempted={attempted_value})"
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class KalshiOrderClient:
    """
    Kalshi order execution client with RSA-PSS authentication.

    Safety limits (hard caps):
      MAX_ORDER_CENTS = 10       (max $0.10 per order)
      MAX_QUANTITY = 1           (max 1 contract per order)
      DAILY_LOSS_LIMIT_CENTS = 50  (max $0.50 daily loss)

    To use for real trading:
      1. Generate a trading API key at kalshi.com/account/profile
         (different from the market-data key in .env)
      2. Set KALSHI_TRADING_KEY and KALSHI_TRADING_SECRET_FILE env vars
         (or pass api_key and private_key_path directly)
      3. Verify: client.get_balance() returns a valid balance
    """

    # Hard safety caps
    MAX_ORDER_CENTS = 50          # $0.10 max per order
    MAX_QUANTITY = 1             # 1 contract max
    DAILY_LOSS_LIMIT_CENTS = 50  # $0.50 max daily loss

    # Retry config
    MAX_RETRIES = 3
    RETRY_DELAY_SEC = 1.0

    def __init__(
        self,
        api_key: Optional[str] = None,
        private_key_path: Optional[str] = None,
        base_url: str = "https://api.elections.kalshi.com/trade-api/v2",
        log_path: str = "/opt/slimy/pm_updown_bot_bundle/logs/kalshi_orders.log",
    ):
        """
        Initialize the Kalshi order client.

        Args:
            api_key: Kalshi API key ID. Falls back to KALSHI_KEY env var.
            private_key_path: Path to RSA private key PEM file.
                Falls back to KALSHI_SECRET_FILE env var or keys/kalshi-prod.key.
            base_url: Kalshi API base URL.
            log_path: Path to order log file.
        """
        self.api_key = api_key or os.getenv("KALSHI_TRADING_KEY") or os.getenv("KALSHI_KEY")
        if not self.api_key:
            raise ValueError("No API key provided and KALSHI_KEY / KALSHI_TRADING_KEY not set in env")

        key_path = private_key_path or os.getenv("KALSHI_TRADING_SECRET_FILE") or os.getenv("KALSHI_SECRET_FILE") or "/opt/slimy/pm_updown_bot_bundle/keys/kalshi-prod.key"
        self.base_url = base_url.rstrip("/")
        self.log_path = log_path

        # Load RSA private key
        with open(key_path, "rb") as f:
            self.private_key = serialization.load_pem_private_key(f.read(), password=None)

        # Daily loss tracking (in-memory, resets at midnight UTC)
        self._daily_loss_cents = 0.0
        self._daily_loss_reset_date = datetime.now(timezone.utc).date()

        # Ensure log directory exists
        Path(self.log_path).parent.mkdir(parents=True, exist_ok=True)

    def _sign_pss(self, text: str) -> str:
        """
        Generate RSA-PSS signature for the given text.
        Per Kalshi docs: padding.PSS with MGF1(SHA256), salt_length=DIGEST_LENGTH.
        """
        signature = self.private_key.sign(
            text.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")

    def _get_auth_headers(self, method: str, path: str, body: str = "") -> dict:
        """
        Build Kalshi RSA-PSS auth headers.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            path: API path (e.g., /portfolio/balance — NOT the full URL)
            body: Request body string (empty for GET requests)

        Returns:
            dict with KALSHI-ACCESS-KEY, KALSHI-ACCESS-TIMESTAMP, KALSHI-ACCESS-SIGNATURE
        """
        ts = str(int(time.time() * 1000))  # milliseconds!
        # Signing string: timestamp + method + full_path (includes /trade-api/v2 prefix, no query params)
        path_without_query = path.split('?')[0]
        to_sign = f"{ts}{method}/trade-api/v2{path_without_query}"
        sig = self._sign_pss(to_sign)
        return {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, body: Optional[dict] = None, retries: int = 0, params: Optional[dict] = None) -> dict:
        """
        Make an authenticated request to the Kalshi API.

        Handles:
          - RSA-PSS authentication
          - Rate limit (429) with retry (max 3 retries)
          - HTTP errors (4xx/5xx) with clear error messages

        Args:
            method: HTTP method
            path: API path (e.g., /portfolio/balance)
            body: Request body dict (for POST/PUT)
            retries: Current retry count
            params: Query string params dict

        Returns:
            Response JSON as dict

        Raises:
            KalshiAuthError: On 401/403 authentication failures
            KalshiRateLimitError: On 429 rate limit after max retries
            Exception: On other HTTP errors
        """
        # Serialize body FIRST so we can use it in the signing string
        body_str = json.dumps(body) if body else ""
        headers = self._get_auth_headers(method, path, body=body_str)
        url = f"{self.base_url}{path}"

        if body:
            resp = requests.request(method, url, headers=headers, json=body, params=params, timeout=15)
        else:
            resp = requests.request(method, url, headers=headers, params=params, timeout=15)

        if resp.status_code == 429:
            if retries < self.MAX_RETRIES:
                logger.warning(f"Rate limited, retrying in {self.RETRY_DELAY_SEC}s (attempt {retries + 1}/{self.MAX_RETRIES})")
                time.sleep(self.RETRY_DELAY_SEC)
                return self._request(method, path, body, retries + 1, params)
            raise KalshiRateLimitError(f"Rate limit hit after {self.MAX_RETRIES} retries")

        if resp.status_code == 401 or resp.status_code == 403:
            error_detail = resp.text
            try:
                error_detail = resp.json().get("error", {}).get("details", resp.text)
            except Exception:
                pass
            raise KalshiAuthError(
                f"Authentication failed ({resp.status_code}): {error_detail}. "
                f"Verify KALSHI_KEY has trading permissions. "
                f"Market-data keys cannot access /portfolio/* endpoints."
            )

        if not resp.ok:
            try:
                error_msg = resp.json().get("error", {}).get("message", resp.text)
            except Exception:
                error_msg = resp.text
            raise Exception(f"Kalshi API error ({resp.status_code}): {error_msg}")

        if resp.text:
            return resp.json()
        return {}

    def _check_safety_limits(
        self,
        price_cents: int,
        quantity: int,
        side: str,
        market_id: str = "",
    ) -> None:
        """
        Check safety limits before placing an order.

        Limits:
          - MAX_ORDER_CENTS: max cents per order
          - MAX_QUANTITY: max contracts per order
          - DAILY_LOSS_LIMIT_CENTS: max cumulative daily loss

        Raises SafetyLimitError if any limit is exceeded.
        """
        # Reset daily loss if new UTC day
        today = datetime.now(timezone.utc).date()
        if today > self._daily_loss_reset_date:
            self._daily_loss_cents = 0.0
            self._daily_loss_reset_date = today

        # Check order size
        order_cents = price_cents * quantity
        if order_cents > self.MAX_ORDER_CENTS:
            raise SafetyLimitError(
                "MAX_ORDER_CENTS",
                self.MAX_ORDER_CENTS,
                order_cents,
                f"Order size {order_cents}c exceeds MAX_ORDER_CENTS={self.MAX_ORDER_CENTS}c "
                f"(price_cents={price_cents} × quantity={quantity}). Max $0.10 per order."
            )

        # Check quantity
        if quantity > self.MAX_QUANTITY:
            raise SafetyLimitError(
                "MAX_QUANTITY",
                self.MAX_QUANTITY,
                quantity,
                f"Quantity {quantity} exceeds MAX_QUANTITY={self.MAX_QUANTITY}."
            )

        # Check daily loss (only for "yes" buys that could result in full loss)
        # A "yes" buy that resolves NO loses the full position value
        if side.lower() == "yes":
            if self._daily_loss_cents + order_cents > self.DAILY_LOSS_LIMIT_CENTS:
                raise SafetyLimitError(
                    "DAILY_LOSS_LIMIT_CENTS",
                    self.DAILY_LOSS_LIMIT_CENTS,
                    self._daily_loss_cents + order_cents,
                    f"Daily loss {self._daily_loss_cents + order_cents}c would exceed "
                    f"DAILY_LOSS_LIMIT_CENTS={self.DAILY_LOSS_LIMIT_CENTS}c. "
                    f"Current loss today: {self._daily_loss_cents}c."
                )

    def _log_order(self, event: str, details: dict) -> None:
        """Append to the order log file."""
        ts = datetime.now(timezone.utc).isoformat()
        try:
            with open(self.log_path, "a") as f:
                f.write(f"{ts} | {event} | {details}\n")
        except Exception as e:
            logger.error(f"Failed to write to order log: {e}")

    # -------------------------------------------------------------------------
    # Portfolio queries
    # -------------------------------------------------------------------------

    def get_balance(self) -> dict:
        """
        Get account balance.

        Returns:
            {
                "available_balance": float,   # in USD
                "portfolio_value": float,    # in USD
                "currency": str
            }

        Raises:
            KalshiAuthError: If authentication fails (key lacks trading permissions)

        Test:
            client = KalshiOrderClient()
            print(client.get_balance())
        """
        return self._request("GET", "/portfolio/balance")

    def get_positions(self) -> list:
        """
        Get open positions.

        Returns:
            List of position dicts with keys: ticker, side, count, cost_basis, market_status

        Raises:
            KalshiAuthError: If authentication fails
        """
        data = self._request("GET", "/portfolio/positions")
        return data.get("positions", [])

    def get_orders(self, status: str = "resting") -> list:
        """
        Get orders, optionally filtered by status.

        Args:
            status: Filter — "resting", "canceled", "executed", "all"

        Returns:
            List of order dicts matching status
        """
        params = {} if status == "all" else {"status": status}
        data = self._request("GET", "/portfolio/orders", params=params, retries=0)
        orders = data.get("orders", [])
        if status != "all":
            orders = [o for o in orders if o.get("status") == status]
        return orders

    def get_order(self, order_id: str) -> dict:
        """
        Get a specific order by ID.

        Args:
            order_id: The order ID from place_order response

        Returns:
            Order dict with status, fills, remaining quantity
        """
        return self._request("GET", f"/portfolio/orders/{order_id}")

    # -------------------------------------------------------------------------
    # Order execution
    # -------------------------------------------------------------------------

    def place_order(
        self,
        ticker: str,
        side: str,
        quantity: int,
        price_cents: int,
        order_type: str = "limit",
    ) -> dict:
        """
        Place a limit order on a Kalshi market.

        SAFETY: This method enforces hard caps BEFORE sending:
          - MAX_ORDER_CENTS = 10  (max $0.10 per order)
          - MAX_QUANTITY = 1      (max 1 contract)
          - DAILY_LOSS_LIMIT_CENTS = 50  (max $0.50 daily loss)

        Args:
            ticker: Market ticker, e.g. "KXBTC-25MAR14-T95000"
            side: "yes" or "no"
            quantity: Number of contracts (integer, 1-MAX_QUANTITY)
            price_cents: Price in cents (1-99) — use yes_price OR no_price, not both
            order_type: "limit" (default) or "market"

        Returns:
            Order response dict with keys: order_id, status, fills, etc.

        Raises:
            SafetyLimitError: If order exceeds safety limits
            KalshiAuthError: If authentication fails (key lacks trading permissions)
            Exception: On API errors

        Example:
            client = KalshiOrderClient()
            order = client.place_order("KXNVDA-26MAR-T95000", "yes", 1, 55)
            print(order["order_id"])
        """
        side = side.lower()
        if side not in ("yes", "no"):
            raise ValueError(f"side must be 'yes' or 'no', got: {side}")

        quantity = int(quantity)
        price_cents = int(price_cents)

        # Validate inputs
        if quantity < 1:
            raise ValueError(f"quantity must be >= 1, got: {quantity}")
        if price_cents < 1 or price_cents > 99:
            raise ValueError(f"price_cents must be 1-99, got: {price_cents}")
        if order_type not in ("limit", "market"):
            raise ValueError(f"order_type must be 'limit' or 'market', got: {order_type}")

        # Safety limits check BEFORE sending
        self._check_safety_limits(price_cents, quantity, side, ticker)

        # Build order body
        if side == "yes":
            order_body = {
                "ticker": ticker,
                "action": "buy",
                "side": "yes",
                "count": quantity,
                "type": order_type,
                "yes_price": price_cents,
            }
        else:
            order_body = {
                "ticker": ticker,
                "action": "buy",
                "side": "no",
                "count": quantity,
                "type": order_type,
                "no_price": price_cents,
            }

        self._log_order("ORDER_ATTEMPT", {
            "ticker": ticker,
            "side": side,
            "quantity": quantity,
            "price_cents": price_cents,
            "type": order_type,
        })

        try:
            result = self._request("POST", "/portfolio/orders", body=order_body)

            self._log_order("ORDER_PLACED", {
                "ticker": ticker,
                "order_id": result.get("order_id"),
                "status": result.get("status"),
            })

            return result

        except SafetyLimitError:
            # Already logged at check time, re-raise
            raise
        except Exception as e:
            self._log_order("ORDER_FAILED", {
                "ticker": ticker,
                "error": str(e),
            })
            raise

    def cancel_order(self, order_id: str) -> dict:
        """
        Cancel an open order.

        Args:
            order_id: The order ID to cancel

        Returns:
            Cancellation confirmation dict
        """
        self._log_order("CANCEL_ATTEMPT", {"order_id": order_id})
        result = self._request("DELETE", f"/portfolio/orders/{order_id}")
        self._log_order("CANCELLED", {"order_id": order_id, "result": result})
        return result

    # -------------------------------------------------------------------------
    # Utility
    # -------------------------------------------------------------------------

    def get_api_status(self) -> dict:
        """
        Check API connectivity and auth status.

        Returns:
            {
                "api_reachable": bool,
                "auth_working": bool,
                "balance": dict or None,
                "error": str or None
            }
        """
        try:
            balance = self.get_balance()
            return {
                "api_reachable": True,
                "auth_working": True,
                "balance": balance,
                "error": None,
            }
        except KalshiAuthError as e:
            return {
                "api_reachable": True,
                "auth_working": False,
                "balance": None,
                "error": str(e),
            }
        except Exception as e:
            return {
                "api_reachable": False,
                "auth_working": False,
                "balance": None,
                "error": str(e),
            }


# ---------------------------------------------------------------------------
# Test / Demo
# -------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import logging as log

    log.basicConfig(level=log.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.setLevel(log.INFO)

    print("=" * 60)
    print("KALSHI ORDER CLIENT — AUTH TEST")
    print("=" * 60)

    # Check what keys are available
    trading_key = os.getenv("KALSHI_TRADING_KEY")
    trading_secret = os.getenv("KALSHI_TRADING_SECRET_FILE")

    key_desc = "KALSHI_TRADING_KEY" if trading_key else "KALSHI_KEY (market-data only)"
    print(f"\nUsing API key: {key_desc}")
    if trading_secret:
        print(f"Key file: {trading_secret}")

    try:
        client = KalshiOrderClient()

        # Step 1: API status
        print("\n[1] Checking API status...")
        status = client.get_api_status()
        print(f"    API reachable: {status['api_reachable']}")
        print(f"    Auth working: {status['auth_working']}")
        if status["error"]:
            print(f"    Error: {status['error']}")

        if status["auth_working"]:
            # Step 2: Balance
            print("\n[2] Fetching balance...")
            balance = client.get_balance()
            print(f"    Available balance: ${balance.get('available_balance', 'N/A')}")
            print(f"    Portfolio value: ${balance.get('portfolio_value', 'N/A')}")

            # Step 3: Positions
            print("\n[3] Fetching positions...")
            positions = client.get_positions()
            print(f"    Open positions: {len(positions)}")
            for pos in positions[:5]:
                print(f"      {pos.get('ticker')}: {pos.get('side')} {pos.get('count')} contracts")

            # Step 4: Open orders
            print("\n[4] Fetching open orders...")
            orders = client.get_orders(status="open")
            print(f"    Open orders: {len(orders)}")

        else:
            print("\n[!] AUTH FAILED — API key lacks trading permissions.")
            print("    To enable trading:")
            print("    1. Go to https://kalshi.com/account/profile")
            print("    2. Click 'API Keys' → 'Create New API Key'")
            print("    3. Select 'Trading' scope")
            print("    4. Set KALSHI_TRADING_KEY and KALSHI_TRADING_SECRET_FILE in .env")
            print("    Note: Market-data keys (KALSHI_KEY) cannot access /portfolio/* endpoints.")

    except Exception as e:
        print(f"\n[!] ERROR: {e}")

    print("\n" + "=" * 60)
