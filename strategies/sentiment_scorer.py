"""AI prior scoring for Kelly sizing in Kalshi strategy.

Cascade order (required):
1) grok_fast
2) grok_420 (disabled — model name deprecated)
3) gemini (primary critic)
4) glm (fallback critic)
"""

from __future__ import annotations

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

CALL_TIMEOUT_SECONDS = 30

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
        "url": "https://api.z.ai/api/paas/v4/chat/completions",
        "model": "glm-4-plus",
        "key_envs": ("GLM_API_KEY", "ZHIPU_API"),
    },
    {
        "name": "gemini",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "model": "gemini-2.5-flash",
        "key_envs": ("GEMINI_API_KEY",),
    },
)

# Cost-aware tier config
TIER_PREMARY_PROVIDER = os.getenv("AI_PRIMARY_PROVIDER", "glm")
TIER_PREMIUM_PROVIDER = os.getenv("AI_PREMIUM_PROVIDER", "grok_fast")
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
                "[debate] Parse failure: provider=%s key_env=%s content=%s",
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
    """Return the Grok (forecaster) provider."""
    for p in PROVIDERS:
        if p["name"] == "grok_fast" or p["name"] == "grok_420":
            key, _ = _first_key(p["key_envs"])
            if key:
                return p
    return None


def _get_critic_provider() -> Optional[dict]:
    """Return the Gemini (primary critic), GLM (fallback), or Grok adversarial."""
    for p in PROVIDERS:
        if p["name"] == "gemini":
            key, _ = _first_key(p["key_envs"])
            if key:
                return p
    for p in PROVIDERS:
        if p["name"] == "glm":
            key, _ = _first_key(p["key_envs"])
            if key:
                return p
    # Fall back to grok_fast but with critic system prompt
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
      2. CRITIC (Gemini primary, GLM fallback, Grok adversarial) — adjusted probability + critique strength
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

