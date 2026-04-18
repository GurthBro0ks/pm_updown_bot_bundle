"""AI prior scoring for Kelly sizing in Kalshi strategy.

Cascade order is driven by config.SENTIMENT_PROVIDERS (not hardcoded).
See config.py for the active provider list and rationale for exclusions.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
from typing import Optional, Tuple

import requests
from dotenv import load_dotenv

# Native Gemini API (OpenAI compat endpoint truncates responses)
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

# Load .env so API keys are available on standalone import
load_dotenv()

logger = logging.getLogger(__name__)

CALL_TIMEOUT_SECONDS = 25

_last_prior_was_fallback = False


def was_last_prior_fallback():
    return _last_prior_was_fallback


class RateLimitError(Exception):
    pass


def _scratchpad_instance():
    from utils.scratchpad import Scratchpad
    return Scratchpad()


try:
    from core.circuit_breaker import BreakerState as BreakerState_v2
except Exception:
    BreakerState_v2 = None

_PROVIDER_REGISTRY = {
    "grok_fast": {
        "name": "grok_fast",
        "url": "https://api.x.ai/v1/chat/completions",
        "model": "grok-4-1-fast-reasoning",
        "key_envs": ("XAI_API_KEY", "GROK_API_KEY", "X_AI_API"),
    },
    "grok_420": {
        "name": "grok_420",
        "url": "https://api.x.ai/v1/chat/completions",
        "model": "grok-4.20-beta-0309-reasoning",
        "key_envs": ("XAI_API_KEY", "GROK_API_KEY", "X_AI_API"),
    },
    "gemini": {
        "name": "gemini",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "model": "gemini-2.5-flash",
        "key_envs": ("GEMINI_API_KEY",),
    },
    "glm": {
        "name": "glm",
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model": "glm-4-flash",
        "key_envs": ("GLM_API_KEY",),
    },
}


def _build_providers():
    try:
        import config as _cfg
        names = _cfg.SENTIMENT_PROVIDERS
    except Exception:
        names = ["grok_420", "gemini"]
    providers = []
    for name in names:
        if name in _PROVIDER_REGISTRY:
            providers.append(_PROVIDER_REGISTRY[name])
        else:
            logger.warning("[scorer] Unknown provider %r in SENTIMENT_PROVIDERS, skipping", name)
    if not providers:
        logger.warning("[scorer] No valid providers configured, falling back to grok_420")
        providers = [_PROVIDER_REGISTRY["grok_420"]]
    return providers


PROVIDERS = _build_providers()

# Cost-aware tier config
TIER_PREMARY_PROVIDER = os.getenv("AI_PRIMARY_PROVIDER", "gemini")
TIER_PREMIUM_PROVIDER = os.getenv("AI_PREMIUM_PROVIDER", "gemini")
# grok_420 deprioritized due to 28.7s/call production latency exhausting
# 300s cascade budget after ~10 markets. Revisit if latency improves.
TIER_PREMIUM_MAX = int(os.getenv("AI_PREMIUM_MAX", "10"))

# ── Circuit Breaker Setup (Module 2) ──────────────────────────────
_breakers = {}
_guarded_providers = {}

def _init_circuit_breakers():
    global _breakers, _guarded_providers
    try:
        from core.circuit_breaker import load_breakers, get_breaker, BreakerConfig
        from core.provider_wrapper import GuardedProvider, ai_prior_validator
        import config as _cfg

        if not _cfg.CIRCUIT_BREAKER_ENABLED:
            return

        defaults = _cfg.CIRCUIT_BREAKER_DEFAULTS
        _breakers = load_breakers(
            _cfg.CIRCUIT_BREAKER_PATH,
            default_config=BreakerConfig(name="__default__", **defaults),
        )

        for provider in PROVIDERS:
            cfg = BreakerConfig(name=provider["name"], **defaults)
            breaker = get_breaker(provider["name"], _breakers, cfg)
            call_fn = functools.partial(_call_provider_unwrapped, provider)
            timeout = _cfg.PROVIDER_CALL_TIMEOUTS.get(provider["name"], CALL_TIMEOUT_SECONDS)
            gp = GuardedProvider(
                provider_name=provider["name"],
                call_fn=call_fn,
                validator=ai_prior_validator,
                breaker=breaker,
                per_call_timeout=timeout,
                skip_exception_types=(RateLimitError,),
            )
            _guarded_providers[provider["name"]] = gp
            gp.reset_run_stats()
        logger.info("[breaker] Initialized %d guarded providers", len(_guarded_providers))
    except Exception as e:
        logger.warning("[breaker] Init failed (%s), breakers disabled", e)


def save_all_breakers():
    try:
        from core.circuit_breaker import save_breakers
        import config as _cfg
        if _breakers and _cfg.CIRCUIT_BREAKER_ENABLED:
            save_breakers(_breakers, _cfg.CIRCUIT_BREAKER_PATH)
            logger.info("[breaker] Saved %d breaker states", len(_breakers))
    except Exception as e:
        logger.warning("[breaker] save_all_breakers failed: %s", e)


def log_breaker_summaries(scratchpad, cron_run_id: str):
    try:
        for name, gp in _guarded_providers.items():
            summary = gp.get_run_summary()
            if scratchpad is not None:
                scratchpad.log(
                    "breaker_summary",
                    cron_run_id=cron_run_id,
                    provider_name=name,
                    calls_this_run=summary["calls_this_run"],
                    successes_this_run=summary["successes_this_run"],
                    failures_this_run=summary["failures_this_run"],
                    short_circuits_this_run=summary["short_circuits_this_run"],
                    avg_latency_seconds=summary["avg_latency_seconds"],
                    final_state=summary["final_state"],
                    success_rate=summary["success_rate"],
                )
    except Exception as e:
        logger.warning("[breaker] log_breaker_summaries failed: %s", e)

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

    # Strategy 1: Try json.loads with fence stripping
    cleaned = raw_text.strip()
    try:
        import json
        # Remove ```json and ``` fences via regex
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
        cleaned = cleaned.strip()
        data = json.loads(cleaned)
        if isinstance(data, dict):
            for key in ("probability", "prob", "p_yes", "yes_probability"):
                if key in data:
                    try:
                        return _clamp_probability(float(data[key]))
                    except (ValueError, TypeError):
                        pass
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Strategy 2: Regex extraction of "probability": NUMBER
    match = re.search(r'"(?:probability|prob|p_yes|yes_probability)"\s*:\s*([\d.]+)', raw_text)
    if match:
        try:
            return _clamp_probability(float(match.group(1)))
        except ValueError:
            pass

    # Strategy 3: Any 0.xxx number as last resort
    match = re.search(r"\b0\.\d+\b", raw_text)
    if match:
        return _clamp_probability(float(match.group(0)))

    return None


def _call_provider(provider: dict, market_text: str, timeout: float = None) -> Optional[float]:
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
        "max_tokens": 500,
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
            timeout=timeout if timeout is not None else CALL_TIMEOUT_SECONDS,
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
                "[kelly] AI prior parse failure: source=%s key_env=%s raw=%r",
                provider["name"],
                key_name,
                content[:500],
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


def _call_provider_unwrapped(provider: dict, market_text: str, timeout: float = None) -> float:
    api_key, key_name = _first_key(provider["key_envs"])
    if not api_key:
        raise ValueError(f"No API key for {provider['name']}")

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
        "max_tokens": 500,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        provider["url"],
        headers=headers,
        json=payload,
        timeout=timeout if timeout is not None else CALL_TIMEOUT_SECONDS,
    )

    if response.status_code == 429:
        raise RateLimitError(f"Rate limited: {provider['name']}")

    response.raise_for_status()

    data = response.json()
    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    prob = _extract_probability(content)
    if prob is None:
        raise ValueError(f"Parse failure: {provider['name']}: {content[:200]}")
    return prob


_init_circuit_breakers()


def _get_providers_for_tier(tier: str):
    """Return ordered provider list based on cost tier."""
    if tier == "bulk":
        primary = [p for p in PROVIDERS if p["name"] == TIER_PREMARY_PROVIDER]
        gemini = [p for p in PROVIDERS if p["name"] == "gemini"]
        return primary + gemini
    elif tier == "premium":
        premium = [p for p in PROVIDERS if p["name"] == TIER_PREMIUM_PROVIDER]
        primary = [p for p in PROVIDERS if p["name"] == TIER_PREMARY_PROVIDER]
        gemini = [p for p in PROVIDERS if p["name"] == "gemini"]
        return premium + primary + gemini
    else:  # skip or unknown
        return []


def _enrich_with_price_context(text: str, ticker: Optional[str]) -> str:
    if not ticker:
        return text
    try:
        from providers.price_feed import enrich_market_text
        text = enrich_market_text(text, market_ticker=ticker)
    except Exception as exc:
        logger.debug("[kelly] Price feed enrichment skipped: %s", exc)
    try:
        from providers.polymarket_signal import enrich_market_text as poly_enrich
        text = poly_enrich(text, kalshi_ticker=ticker)
    except Exception as exc:
        logger.debug("[kelly] Polymarket enrichment skipped: %s", exc)
    return text


def get_ai_prior(
    market_text: str,
    tier: str = "premium",
    market_ticker: str = None,
    timeout: float = None,
) -> float:
    """Return calibrated YES probability in [0,1].

    Args:
        market_text: Market question/title text
        tier: "premium" (grok+gemini fallback), "bulk" (gemini only), "skip" (return 0.5)
        market_ticker: Optional ticker for logging
        timeout: Per-call timeout in seconds. If provided, overrides the default
                 CALL_TIMEOUT_SECONDS for this individual call. Useful when budget
                 is running low and we don't want a slow provider to blow past budget.
    """
    normalized = (market_text or "").strip()
    ticker_str = f" market: {market_ticker}" if market_ticker else ""

    if tier == "skip":
        logger.info("[kelly] AI prior: source=rate_limited prob=0.500 (skipped%s)", ticker_str)
        global _last_prior_was_fallback
        _last_prior_was_fallback = True
        return 0.5

    if not normalized:
        logger.warning("[kelly] AI prior: source=fallback_empty prob=0.500%s", ticker_str)
        _last_prior_was_fallback = True
        return 0.5

    normalized = _enrich_with_price_context(normalized, market_ticker)

    providers = _get_providers_for_tier(tier)
    if not providers:
        logger.info("[kelly] AI prior: source=rate_limited prob=0.500 (skipped%s)", ticker_str)
        _last_prior_was_fallback = True
        return 0.5

    # ── Circuit breaker path ──────────────────────────────────────
    if _guarded_providers:
        provider_names = [p["name"] for p in providers]
        has_any_key = any(_first_key(p["key_envs"])[0] for p in providers)
        all_short_circuit = True
        provider_states = {}

        for pname in provider_names:
            gp = _guarded_providers.get(pname)
            if gp is None:
                continue
            result, status = gp.call(normalized, timeout=timeout)
            provider_states[pname] = gp.breaker.state.name

            if status == "short_circuit":
                continue
            if status == "rate_limit":
                continue

            all_short_circuit = False

            if status == "success" and result is not None:
                logger.info("[kelly] AI prior: source=%s prob=%.3f (%s%s)", pname, result, tier, ticker_str)
                _last_prior_was_fallback = False
                return result

        # All providers failed or short-circuited
        all_open = all(
            _guarded_providers.get(pn) and
            _guarded_providers[pn].breaker.state in (BreakerState_v2.OPEN, BreakerState_v2.HALF_OPEN)
            for pn in provider_names
            if pn in _guarded_providers
        ) if provider_names else False

        if all_short_circuit:
            logger.warning("[kelly] cascade_all_providers_down: all short-circuited%s ticker=%s", ticker_str, market_ticker)
            try:
                _scratchpad_instance().log(
                    "cascade_all_providers_down",
                    ticker=market_ticker,
                    provider_states=provider_states,
                )
            except Exception:
                pass

        if not has_any_key:
            logger.warning("[kelly] AI prior: source=fallback_no_keys prob=0.500%s", ticker_str)
        else:
            logger.warning("[kelly] AI prior: source=fallback_all_failed prob=0.500%s", ticker_str)
        _last_prior_was_fallback = True
        return 0.5

    # ── Legacy path (no circuit breakers) ─────────────────────────
    has_any_key = False
    for provider in providers:
        key, _ = _first_key(provider["key_envs"])
        if key:
            has_any_key = True
        prob = _call_provider(provider, normalized, timeout=timeout)
        if prob is not None:
            logger.info("[kelly] AI prior: source=%s prob=%.3f (%s%s)", provider["name"], prob, tier, ticker_str)
            _last_prior_was_fallback = False
            return prob

    if not has_any_key:
        logger.warning("[kelly] AI prior: source=fallback_no_keys prob=0.500%s", ticker_str)
    else:
        logger.warning("[kelly] AI prior: source=fallback_all_failed prob=0.500%s", ticker_str)
    _last_prior_was_fallback = True
    return 0.5


def get_ai_stock_sentiment(headline_text, ticker=None):
    """Stub for stock sentiment scoring. Returns neutral 0.5."""
    return 0.5


def get_bayesian_prior(market: dict, providers: list = None, tier: str = "premium") -> float:
    """Compatibility wrapper for legacy callsite compatibility."""
    _ = providers  # Unused; kept for callsite compatibility.
    text = market.get("title") or market.get("question") or market.get("description") or ""
    ticker = market.get("ticker") or market.get("id")
    return get_ai_prior(text, tier=tier, market_ticker=ticker)


# ---------------------------------------------------------------------------
# Multi-model debate (Forecaster / Critic / Synthesizer)
# ---------------------------------------------------------------------------

DEBATE_MODE = os.getenv("DEBATE_MODE", "false").lower() == "true"
DEBATE_TIMEOUT_SECONDS = 15

FORECASTER_SYSTEM = (
    "You are a quantitative forecaster for prediction markets. "
    "Given the market question, current price, and context, "
    "estimate the TRUE probability this event resolves YES. Be precise. "
    "Output ONLY valid JSON: {\"probability\": float 0-1, \"confidence\": float 0-1, "
    "\"reasoning\": string (2-3 sentences max)}"
)

CRITIC_SYSTEM = (
    "You are a skeptical risk analyst. Given this market question "
    "and a forecaster's probability estimate of {forecaster_prob}, "
    "identify the strongest reasons this estimate could be WRONG. "
    "Consider: base rate neglect, recency bias, missing information, market efficiency. "
    "Output ONLY valid JSON: {\"adjusted_probability\": float 0-1, "
    "\"critique_strength\": float 0-1 (how compelling your objections are), "
    "\"counterarguments\": string (2-3 sentences max)}"
)

DEBATE_FORECASTER_PROMPT = """Market question: {market_question}
Current price: {market_price}
Context: {context}

Estimate the true probability this resolves YES. Output JSON only."""

DEBATE_CRITIC_PROMPT = """Market question: {market_question}
Forecaster's probability estimate: {forecaster_prob}

Identify the strongest reasons this estimate could be WRONG. Output JSON only."""


def _extract_json_flexible(raw_text: str) -> Optional[dict]:
    """Parse AI JSON response with regex fallback for unreliable JSON producers."""
    if not raw_text:
        return None

    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = re.sub(r"^json\s*", "", text, flags=re.IGNORECASE).strip()

    # Try direct JSON parse first
    try:
        return json.loads(text)
    except Exception:
        pass

    # Regex fallback: extract top-level key-value pairs
    result = {}
    prob_match = re.search(
        r'"(?:probability|prob|p_yes|adjusted_probability|forecaster_prob|adjusted_prob)"\s*:\s*([\d.]+)',
        text,
    )
    if prob_match:
        try:
            result["probability"] = float(prob_match.group(1))
        except Exception:
            pass

    conf_match = re.search(r'"(?:confidence|critique_strength|strength)"\s*:\s*([\d.]+)', text)
    if conf_match:
        try:
            result["confidence"] = float(conf_match.group(1))
        except Exception:
            pass

    reason_match = re.search(r'"(?:reasoning|counterarguments|reason)"\s*:\s*"([^"]+)"', text)
    if reason_match:
        result["reasoning"] = reason_match.group(1)

    return result if result else None


def _call_debate_role(
    provider: dict,
    system_prompt: str,
    user_prompt: str,
    timeout: int = DEBATE_TIMEOUT_SECONDS,
) -> Optional[dict]:
    """Call a debate role provider, return parsed JSON or None."""
    api_key, key_name = _first_key(provider["key_envs"])
    if not api_key:
        return None

    # ── Gemini: use native API (OpenAI compat endpoint truncates) ────────────
    if provider["name"] == "gemini" and GEMINI_AVAILABLE:
        return _call_gemini_native(provider, system_prompt, user_prompt, timeout)

    # ── All other providers: OpenAI-compatible ───────────────────────────────
    payload = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 1500,
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
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        parsed = _extract_json_flexible(content)
        if parsed is None:
            logger.warning(
                "[debate] Parse failure: provider=%s key_env=%s raw=%r",
                provider["name"],
                key_name,
                content[:100],
            )
        return parsed
    except Exception as exc:
        logger.warning(
            "[debate] Request failed: provider=%s key_env=%s err=%s",
            provider["name"],
            key_name,
            exc,
        )
        return None


def _call_gemini_native(
    provider: dict,
    system_prompt: str,
    user_prompt: str,
    timeout: int = DEBATE_TIMEOUT_SECONDS,
) -> Optional[dict]:
    """Call Gemini using native API (avoids OpenAI compat truncation issues)."""
    try:
        genai.configure(api_key=os.getenv("GEMINI_API_KEY", ""))
        model = genai.GenerativeModel("gemini-2.5-flash")
        combined = f"{system_prompt}\n\n{user_prompt}"
        response = model.generate_content(
            combined,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        parsed = json.loads(response.text)
        return parsed
    except Exception as exc:
        logger.warning("[debate] Gemini native call failed: %s", exc)
        return None


def _get_forecaster_provider() -> Optional[dict]:
    """Return the Grok (forecaster) provider from registry."""
    for name in ("grok_420", "grok_fast"):
        p = _PROVIDER_REGISTRY.get(name)
        if p:
            key, _ = _first_key(p["key_envs"])
            if key:
                return p
    return None


def _get_critic_provider() -> Optional[dict]:
    """Return the Gemini (primary critic) or Grok adversarial."""
    p = _PROVIDER_REGISTRY.get("gemini")
    if p:
        key, _ = _first_key(p["key_envs"])
        if key:
            return p
    return _get_forecaster_provider()


def _synthesize(
    forecaster_prob: float,
    critic_prob: float,
    critique_strength: float,
) -> Tuple[float, str, float]:
    """
    Synthesize final probability via weighted average.

    Default: w_forecaster=0.6, w_critic=0.4
    If |diff| > 0.25: flag as disagree
    If critique_strength > 0.7: shift weight to w_forecaster=0.4, w_critic=0.6

    Returns: (final_prob, consensus, debate_confidence)
    """
    diff = abs(forecaster_prob - critic_prob)
    high_disagreement = diff > 0.25
    consensus = "disagree" if high_disagreement else "agree"

    if critique_strength > 0.7:
        w_forecaster = 0.4
        w_critic = 0.6
    else:
        w_forecaster = 0.6
        w_critic = 0.4

    final_prob = w_forecaster * forecaster_prob + w_critic * critic_prob
    final_prob = _clamp_probability(final_prob)

    # debate_confidence = min(forecaster_confidence, 1 - critique_strength)
    # We'll use critique_strength as proxy for uncertainty
    debate_confidence = max(0.0, 1.0 - critique_strength)

    return final_prob, consensus, debate_confidence


def multi_model_debate(
    market_question: str,
    market_price: float = 0.5,
    context: str = "",
) -> dict:
    """
    Run a 3-role adversarial debate to produce a final AI probability estimate.

    Roles:
      1. FORECASTER (Grok primary) — direct probability estimate
      2. CRITIC (Gemini primary, Grok adversarial) — adjusted probability + critique strength
      3. SYNTHESIZER (local math) — weighted average with disagreement flag

    Args:
        market_question: The market question text
        market_price: Current Kalshi market price (0-1)
        context: Additional context (news, data, etc.)

    Returns:
        {
            "debate_probability": float,   # final weighted prob
            "forecaster_prob": float,
            "forecaster_confidence": float,
            "forecaster_reasoning": str,
            "critic_prob": float,
            "critique_strength": float,
            "critic_reasoning": str,
            "consensus": "agree" | "disagree",
            "debate_confidence": float,
            "debate_reasoning": str,
            "debate_used": bool,           # True if both roles responded
        }
    """
    if not DEBATE_MODE:
        return {
            "debate_probability": market_price,
            "forecaster_prob": market_price,
            "forecaster_confidence": 0.5,
            "forecaster_reasoning": "",
            "critic_prob": market_price,
            "critique_strength": 0.0,
            "critic_reasoning": "",
            "consensus": "unknown",
            "debate_confidence": 0.5,
            "debate_reasoning": "debate_mode_disabled",
            "debate_used": False,
        }

    # ── Role 1: Forecaster ───────────────────────────────────────────────────
    forecaster_provider = _get_forecaster_provider()
    forecaster_result = None
    if forecaster_provider:
        forecaster_result = _call_debate_role(
            forecaster_provider,
            FORECASTER_SYSTEM,
            DEBATE_FORECASTER_PROMPT.format(
                market_question=market_question,
                market_price=market_price,
                context=context or "None provided",
            ),
        )

    if forecaster_result is None:
        logger.warning("[debate] Forecaster call failed — falling back to single-model prior")
        return {
            "debate_probability": market_price,
            "forecaster_prob": market_price,
            "forecaster_confidence": 0.5,
            "forecaster_reasoning": "forecaster_call_failed",
            "critic_prob": market_price,
            "critique_strength": 0.0,
            "critic_reasoning": "",
            "consensus": "unknown",
            "debate_confidence": 0.5,
            "debate_reasoning": "single_model_fallback",
            "debate_used": False,
        }

    forecaster_prob = _clamp_probability(
        float(forecaster_result.get("probability", market_price))
    )
    forecaster_confidence = _clamp_probability(
        float(forecaster_result.get("confidence", 0.5))
    )
    forecaster_reasoning = str(forecaster_result.get("reasoning", ""))

    # ── Role 2: Critic ───────────────────────────────────────────────────────
    critic_provider = _get_critic_provider()
    is_critic_grok = (critic_provider and critic_provider["name"] in ("grok_fast", "grok_420"))

    # Inject the forecaster probability into the critic system prompt
    critic_system = CRITIC_SYSTEM.replace("{forecaster_prob}", str(forecaster_prob))

    critic_result = None
    if critic_provider:
        critic_result = _call_debate_role(
            critic_provider,
            critic_system,
            DEBATE_CRITIC_PROMPT.format(
                market_question=market_question,
                forecaster_prob=forecaster_prob,
            ),
        )

    if critic_result is None:
        logger.warning("[debate] Critic call failed — using forecaster only")
        return {
            "debate_probability": forecaster_prob,
            "forecaster_prob": forecaster_prob,
            "forecaster_confidence": forecaster_confidence,
            "forecaster_reasoning": forecaster_reasoning,
            "critic_prob": forecaster_prob,
            "critique_strength": 0.0,
            "critic_reasoning": "critic_call_failed",
            "consensus": "agree",
            "debate_confidence": forecaster_confidence,
            "debate_reasoning": forecaster_reasoning,
            "debate_used": True,
        }

    critic_prob = _clamp_probability(
        float(critic_result.get("adjusted_probability", forecaster_prob))
    )
    critique_strength = _clamp_probability(
        float(critic_result.get("critique_strength", 0.0))
    )
    critic_reasoning = str(critic_result.get("counterarguments", ""))

    # ── Role 3: Synthesizer ──────────────────────────────────────────────────
    final_prob, consensus, debate_confidence = _synthesize(
        forecaster_prob, critic_prob, critique_strength
    )

    combined_reasoning = (
        f"FORECASTER: {forecaster_reasoning} "
        f"CRITIC: {critic_reasoning}"
    )

    result = {
        "debate_probability": round(final_prob, 4),
        "forecaster_prob": round(forecaster_prob, 4),
        "forecaster_confidence": round(forecaster_confidence, 4),
        "forecaster_reasoning": forecaster_reasoning,
        "critic_prob": round(critic_prob, 4),
        "critique_strength": round(critique_strength, 4),
        "critic_reasoning": critic_reasoning,
        "consensus": consensus,
        "debate_confidence": round(debate_confidence, 4),
        "debate_reasoning": combined_reasoning,
        "debate_used": True,
    }

    logger.info(
        "[debate] result: consensus=%s final_prob=%.4f (forecaster=%.4f critic=%.4f) "
        "critique_strength=%.2f",
        consensus,
        final_prob,
        forecaster_prob,
        critic_prob,
        critique_strength,
    )

    return result

