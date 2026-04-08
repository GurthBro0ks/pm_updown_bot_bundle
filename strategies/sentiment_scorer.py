"""AI prior scoring for Kelly sizing in Kalshi strategy.

Cascade order (required):
1) grok_fast
2) grok_420 (disabled — model name deprecated)
3) glm
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

CALL_TIMEOUT_SECONDS = 10

PROVIDERS = (
    {
        "name": "grok_fast",
        "url": "https://api.x.ai/v1/chat/completions",
        "model": "grok-4-1-fast-reasoning",
        "key_envs": ("XAI_API_KEY", "GROK_API_KEY", "X_AI_API"),
    },
    {
        "name": "grok_420",
        "url": "https://api.x.ai/v1/chat/completions",
        "model": "grok-4.20-beta-0309-reasoning",
        "key_envs": ("XAI_API_KEY", "GROK_API_KEY", "X_AI_API"),
    },
    {
        "name": "glm",
        "url": "https://api.z.ai/api/coding/paas/v4/chat/completions",
        "model": "glm-5.1",
        "key_envs": ("GLM_API_KEY", "ZHIPU_API"),
    },
)

# Cost-aware tier config
TIER_PREMARY_PROVIDER = os.getenv("AI_PRIMARY_PROVIDER", "glm")
TIER_PREMIUM_PROVIDER = os.getenv("AI_PREMIUM_PROVIDER", "grok_420")
TIER_PREMIUM_MAX = int(os.getenv("AI_PREMIUM_MAX", "10"))

PROMPT_SYSTEM = (
    "You estimate prediction market YES probabilities. "
    "Respond with ONLY JSON: {\"probability\": <0.0-1.0>}"
)


def _first_key(env_names: Tuple[str, ...]) -> Tuple[Optional[str], Optional[str]]:
    for env_name in env_names:
        value = (os.getenv(env_name) or "").strip()
        if value and value.lower() != "your_key_here":
            return value, env_name
    return None, None


def _clamp_probability(value: float) -> float:
    return max(0.0, min(1.0, value))


def _extract_probability(raw_text: str) -> Optional[float]:
    if not raw_text:
        return None

    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json", "", 1).strip()

    parsed: Optional[dict] = None
    try:
        maybe = json.loads(text)
        if isinstance(maybe, dict):
            parsed = maybe
    except Exception:
        parsed = None

    if parsed is not None:
        for key in ("probability", "prob", "p_yes", "yes_probability"):
            if key in parsed:
                try:
                    return _clamp_probability(float(parsed[key]))
                except Exception:
                    pass

    match = re.search(r"(?<!\d)(0(?:\.\d+)?|1(?:\.0+)?)(?!\d)", text)
    if match:
        try:
            return _clamp_probability(float(match.group(1)))
        except Exception:
            return None

    return None


def _call_provider(provider: dict, market_text: str) -> Optional[float]:
    api_key, key_name = _first_key(provider["key_envs"])
    if not api_key:
        return None

    payload = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": PROMPT_SYSTEM},
            {
                "role": "user",
                "content": (
                    "Market text:\n"
                    f"{market_text}\n\n"
                    "Return a calibrated YES probability only as JSON."
                ),
            },
        ],
        "temperature": 0.0,
        "max_tokens": 80,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            provider["url"],
            headers=headers,
            json=payload,
            timeout=CALL_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        prob = _extract_probability(content)
        if prob is None:
            logger.warning(
                "[kelly] AI prior parse failure: source=%s key_env=%s",
                provider["name"],
                key_name,
            )
            return None
        return prob
    except Exception as exc:
        logger.warning(
            "[kelly] AI prior request failed: source=%s key_env=%s err=%s",
            provider["name"],
            key_name,
            exc,
        )
        return None


def _get_providers_for_tier(tier: str):
    """Return ordered provider list based on cost tier."""
    if tier == "bulk":
        return [p for p in PROVIDERS if p["name"] == TIER_PREMARY_PROVIDER]
    elif tier == "premium":
        premium = [p for p in PROVIDERS if p["name"] == TIER_PREMIUM_PROVIDER]
        primary = [p for p in PROVIDERS if p["name"] == TIER_PREMARY_PROVIDER]
        return premium + primary
    else:  # skip or unknown
        return []


def get_ai_prior(market_text: str, tier: str = "premium", market_ticker: str = None) -> float:
    """Return calibrated YES probability in [0,1].

    Args:
        market_text: Market question/title text
        tier: "premium" (grok+glm fallback), "bulk" (glm only), "skip" (return 0.5)
        market_ticker: Optional ticker for logging
    """
    normalized = (market_text or "").strip()
    ticker_str = f" market: {market_ticker}" if market_ticker else ""

    if tier == "skip":
        logger.info("[kelly] AI prior: source=rate_limited prob=0.500 (skipped%s)", ticker_str)
        return 0.5

    if not normalized:
        logger.warning("[kelly] AI prior: source=fallback_empty prob=0.500%s", ticker_str)
        return 0.5

    providers = _get_providers_for_tier(tier)
    if not providers:
        logger.info("[kelly] AI prior: source=rate_limited prob=0.500 (skipped%s)", ticker_str)
        return 0.5

    has_any_key = False
    for provider in providers:
        key, _ = _first_key(provider["key_envs"])
        if key:
            has_any_key = True
        prob = _call_provider(provider, normalized)
        if prob is not None:
            logger.info("[kelly] AI prior: source=%s prob=%.3f (%s%s)", provider["name"], prob, tier, ticker_str)
            return prob

    if not has_any_key:
        logger.warning("[kelly] AI prior: source=fallback_no_keys prob=0.500%s", ticker_str)
    else:
        logger.warning("[kelly] AI prior: source=fallback_all_failed prob=0.500%s", ticker_str)
    return 0.5


def get_ai_stock_sentiment(headline_text, ticker=None):
    """Stub for stock sentiment scoring. Returns neutral 0.5."""
    return 0.5


def get_bayesian_prior(market: dict, providers: list = None, tier: str = "premium") -> float:
    """Compatibility wrapper for legacy callsites."""
    _ = providers  # Unused; kept for callsite compatibility.
    text = market.get("title") or market.get("question") or market.get("description") or ""
    ticker = market.get("ticker") or market.get("id")
    return get_ai_prior(text, tier=tier, market_ticker=ticker)

