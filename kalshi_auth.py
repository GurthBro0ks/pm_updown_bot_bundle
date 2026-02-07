#!/usr/bin/env python3
"""
Kalshi RSA Authentication Helper

Generates KALSHI-ACCESS-* headers using RSA-PKCS1v15-SHA256 signatures.
All Kalshi API calls must use these headers instead of Basic auth.

Usage:
    from kalshi_auth import KalshiAuth

    auth = KalshiAuth(api_key="your-key", private_key_path="/path/to/key.pem")
    headers = auth.get_headers("GET", "/trade-api/v2/exchange/status")
    resp = requests.get("https://api.elections.kalshi.com/trade-api/v2/exchange/status",
                        headers=headers)
"""

import base64
import os
import time

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

KALSHI_BASE_URL = "https://api.elections.kalshi.com"


class KalshiAuth:
    """Kalshi RSA authentication handler."""

    def __init__(self, api_key: str = None, private_key_path: str = None):
        """
        Initialize with API key and RSA private key.

        Args:
            api_key: Kalshi API key (or set KALSHI_KEY env var)
            private_key_path: Path to PEM-encoded RSA private key
                              (or set KALSHI_PRIVATE_KEY_PATH env var)
        """
        self.api_key = api_key or os.environ.get("KALSHI_KEY", "")
        key_path = private_key_path or os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")

        if not self.api_key:
            raise ValueError("Kalshi API key required: pass api_key or set KALSHI_KEY")
        if not key_path:
            raise ValueError(
                "RSA private key path required: pass private_key_path "
                "or set KALSHI_PRIVATE_KEY_PATH"
            )

        with open(key_path, "rb") as f:
            self._private_key = serialization.load_pem_private_key(
                f.read(), password=None
            )

    def get_headers(self, method: str, path: str) -> dict:
        """
        Build signed headers for a Kalshi API request.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.) -- uppercase
            path: API path starting with / (e.g. /trade-api/v2/exchange/status)

        Returns:
            Dict with KALSHI-ACCESS-KEY, KALSHI-ACCESS-SIGNATURE,
            KALSHI-ACCESS-TIMESTAMP headers.
        """
        timestamp = str(int(time.time() * 1000))
        msg_string = timestamp + method.upper() + path

        signature = self._private_key.sign(
            msg_string.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        sig_b64 = base64.b64encode(signature).decode()

        return {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-SIGNATURE": sig_b64,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Convenience wrappers
    # ------------------------------------------------------------------

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        """
        Make an authenticated request to the Kalshi API.

        Args:
            method: HTTP method
            path: API path (e.g. /trade-api/v2/exchange/status)
            **kwargs: Extra args forwarded to requests.request (json, params, etc.)

        Returns:
            requests.Response
        """
        headers = self.get_headers(method.upper(), path)
        # Merge any caller-supplied headers
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))

        url = KALSHI_BASE_URL + path
        return requests.request(method.upper(), url, headers=headers, **kwargs)

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return self.request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs) -> requests.Response:
        return self.request("DELETE", path, **kwargs)


# ------------------------------------------------------------------
# Standalone helper (functional API matching the spec in the prompt)
# ------------------------------------------------------------------

def kalshi_auth_headers(
    method: str,
    path: str,
    api_key: str = None,
    private_key_path: str = None,
) -> dict:
    """
    Generate Kalshi RSA-signed auth headers (functional interface).

    Args:
        method: HTTP method (GET, POST, etc.)
        path: API path starting with /
        api_key: Kalshi API key (falls back to KALSHI_KEY env)
        private_key_path: Path to PEM private key (falls back to KALSHI_PRIVATE_KEY_PATH env)

    Returns:
        Dict of auth headers.
    """
    api_key = api_key or os.environ.get("KALSHI_KEY", "")
    key_path = private_key_path or os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")

    with open(key_path, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)

    timestamp = str(int(time.time() * 1000))
    msg_string = timestamp + method.upper() + path

    signature = private_key.sign(
        msg_string.encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    sig_b64 = base64.b64encode(signature).decode()

    return {
        "KALSHI-ACCESS-KEY": api_key,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }
