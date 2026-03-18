"""
Sentiment Scorer — AI-powered probability estimation for prediction markets.

Provider cascade (TESTING — MiniMax primary):
  1. MiniMax M2.5 — reasoning model, already paid for, $0.30/$1.20 per M tokens
  2. GLM-4-Flash — FREE emergency fallback

Production cascade (swap SENTIMENT_PROVIDERS in config.py when Grok key added):
  1. Grok 4.20 Beta (reasoning) — flagship, lowest hallucination, real-time X search
  2. Grok 4.1 Fast (reasoning) — cheap fallback, same API key
  3. MiniMax M2.5 — reasoning model backup
  4. GLM-4-Flash — FREE last resort

All providers use OpenAI-compatible API format.
To switch: change SENTIMENT_PROVIDERS in config.py
"""

import os
import json
import time
import hashlib
import logging
from pathlib import Path
from typing import Optional
from openai import OpenAI

logger = logging.getLogger("sentiment_scorer")

# ─── Provider configs ─────────────────────────────────────────
PROVIDERS = {
    "minimax": {
        "base_url": "https://api.minimax.io/v1/text",
        "api_key_env": "MINIMAX_API_KEY",
        "model": "MiniMax-M2.5",
        "timeout": 45,
        "supports_search": False,
        "endpoint": "/chatcompletion_v2",
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "GLM_API_KEY",
        "model": "glm-4.6",
        "timeout": 30,
        "supports_search": False,
    },
    "grok_420": {
        "base_url": "https://api.x.ai/v1",
        "api_key_env": "GROK_API_KEY",
        "model": "grok-4.20-beta-0309-reasoning",
        "timeout": 45,
        "supports_search": True,
    },
    "grok_fast": {
        "base_url": "https://api.x.ai/v1",
        "api_key_env": "GROK_API_KEY",
        "model": "grok-4-1-fast-reasoning",
        "timeout": 30,
        "supports_search": True,
    },
}

# ─── Cache (file-based, survives restarts) ─────────────────────
CACHE_DIR = Path(__file__).parent.parent / "data" / "sentiment_cache"
CACHE_TTL = 600  # 10 minutes — matches cron cycle


def _cache_key(market_id: str, market_desc: str) -> str:
    h = hashlib.md5(f"{market_id}:{market_desc}".encode()).hexdigest()[:12]
    return f"{market_id}_{h}"


def _get_cached(key: str) -> Optional[dict]:
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        data = json.loads(path.read_text())
        if time.time() - data.get("ts", 0) < CACHE_TTL:
            return data
    return None


def _set_cache(key: str, data: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data["ts"] = time.time()
    (CACHE_DIR / f"{key}.json").write_text(json.dumps(data))


# ─── Prompt engineering ────────────────────────────────────────
SYSTEM_PROMPT = """You are a calibrated probability estimator for prediction markets.

Your job: Given a market question, estimate the probability (0.00 to 1.00) that it resolves YES.

Rules:
1. Use all available information including current events, historical patterns, and base rates.
2. Be CALIBRATED — when you say 70%, events like that should happen ~70% of the time.
3. Account for the time remaining before resolution.
4. Consider market manipulation, selection bias, and information asymmetry.
5. If you truly cannot estimate, return 0.50 (maximum uncertainty).

Respond ONLY with valid JSON. No markdown, no explanation outside the JSON.

Format:
{
  "probability": 0.XX,
  "confidence": "low|medium|high",
  "reasoning": "1-2 sentence summary of key factors",
  "sentiment": "bullish|bearish|neutral",
  "key_factors": ["factor1", "factor2"]
}"""


def _build_market_prompt(market: dict) -> str:
    parts = [f"Market: {market.get('title', market.get('question', 'Unknown'))}"]
    if market.get("description"):
        parts.append(f"Description: {market['description'][:500]}")
    if market.get("category"):
        parts.append(f"Category: {market['category']}")
    if market.get("end_date"):
        parts.append(f"Resolves: {market['end_date']}")
    if market.get("current_price"):
        parts.append(f"Current market price: {market['current_price']}")
    parts.append("\nEstimate the probability this resolves YES.")
    return "\n".join(parts)


# ─── API call with fallback ────────────────────────────────────
def _call_provider(provider_name: str, prompt: str) -> Optional[dict]:
    cfg = PROVIDERS.get(provider_name)
    if not cfg:
        return None

    api_key = os.environ.get(cfg["api_key_env"], "")
    if not api_key:
        logger.warning(f"[scorer] {provider_name}: no API key ({cfg['api_key_env']})")
        return None

    try:
        # Handle MiniMax custom endpoint separately
        if provider_name == "minimax":
            import httpx
            url = f"{cfg['base_url']}/chatcompletion_v2"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": cfg["model"],
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 300,
                "temperature": 0.3
            }
            response = httpx.post(url, json=payload, headers=headers, timeout=cfg["timeout"])
            response.raise_for_status()
            data = response.json()
            raw = data["choices"][0]["message"]["content"].strip()
        else:
            # Standard OpenAI-compatible API
            client = OpenAI(
                base_url=cfg["base_url"],
                api_key=api_key,
                timeout=cfg["timeout"],
            )

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]

            response = client.chat.completions.create(
                model=cfg["model"],
                messages=messages,
                max_tokens=300,
                temperature=0.3,
            )

            raw = response.choices[0].message.content.strip()

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        result = json.loads(raw)
        prob = float(result.get("probability", 0.5))
        prob = max(0.01, min(0.99, prob))
        result["probability"] = prob
        result["provider"] = provider_name

        logger.info(f"[scorer] {provider_name}: prob={prob:.2f} conf={result.get('confidence', '?')}")
        return result

    except json.JSONDecodeError as e:
        logger.warning(f"[scorer] {provider_name}: JSON parse error: {e}")
        return None
    except Exception as e:
        logger.warning(f"[scorer] {provider_name}: API error: {e}")
        return None


def score_market(market: dict, providers: list = None) -> dict:
    """Score a single market. Tries providers in order until one succeeds."""
    if providers is None:
        try:
            from config import SENTIMENT_PROVIDERS
            providers = SENTIMENT_PROVIDERS
        except ImportError:
            providers = ["minimax", "glm"]

    market_id = market.get("id", market.get("ticker", "unknown"))
    market_desc = market.get("title", market.get("question", ""))

    ck = _cache_key(market_id, market_desc)
    cached = _get_cached(ck)
    if cached:
        cached["cached"] = True
        return cached

    prompt = _build_market_prompt(market)

    for provider in providers:
        result = _call_provider(provider, prompt)
        if result:
            result["market_id"] = market_id
            result["cached"] = False
            _set_cache(ck, result)
            return result

    logger.warning(f"[scorer] All providers failed for {market_id}, returning 0.50")
    return {
        "market_id": market_id,
        "probability": 0.50,
        "confidence": "none",
        "reasoning": "All API providers failed. Using flat prior.",
        "sentiment": "neutral",
        "key_factors": [],
        "provider": "fallback",
        "cached": False,
    }


def score_markets(markets: list, providers: list = None, max_markets: int = 50) -> list:
    """Score multiple markets with rate limit delays."""
    results = []
    scored = 0
    for market in markets[:max_markets]:
        result = score_market(market, providers)
        results.append(result)
        scored += 1
        if not result.get("cached") and scored < len(markets):
            time.sleep(0.5)
    logger.info(f"[scorer] Scored {scored} markets ({sum(1 for r in results if r.get('cached'))} cached)")
    return results


def get_bayesian_prior(market: dict, providers: list = None) -> float:
    """Drop-in replacement for flat 0.50 prior in BayesianEstimate."""
    result = score_market(market, providers)
    return result["probability"]


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    test_market = {
        "id": "test-fed-rate-cut",
        "title": "Will the Fed cut interest rates at their June 2026 meeting?",
        "category": "Economics",
        "end_date": "2026-06-18",
        "current_price": 0.42,
    }

    print("=== Sentiment Scorer Test ===")
    result = score_market(test_market)
    print(json.dumps(result, indent=2))

    if result["provider"] != "fallback":
        print(f"\n✓ PASS — provider={result['provider']} prob={result['probability']:.2f}")
    else:
        print(f"\n✗ FAIL — all providers failed")
        sys.exit(1)
