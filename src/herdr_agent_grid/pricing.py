"""Offline token-price estimates; exact model IDs, never a family-price guess.

USD per million tokens, verified 2026-10-04 against:
https://platform.claude.com/docs/en/about-claude/pricing
https://developers.openai.com/api/docs/pricing
https://developers.openai.com/api/docs/models/gpt-6.1-sol
https://developers.openai.com/api/docs/models/gpt-6-sol
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

VERIFIED = "2026-10-04"


@dataclass(frozen=True)
class Rates:
    input: float
    output: float
    read: float
    write: float
    write_hour: float = 0
    long_context: bool = False


RATES = {
    "claude-opus-5-5": Rates(4, 20, .20, 5, 8),
    "claude-sonnet-5-5": Rates(2, 10, .20, 2.50, 4),
    "claude-opus-5": Rates(5, 25, .50, 6.25, 10),
    "claude-opus-4-8": Rates(5, 25, .50, 6.25, 10),
    "claude-opus-4-7": Rates(5, 25, .50, 6.25, 10),
    "claude-opus-4-6": Rates(5, 25, .50, 6.25, 10),
    "claude-sonnet-5": Rates(2, 10, .20, 2.50, 4),
    "claude-sonnet-4-6": Rates(3, 15, .30, 3.75, 6),
    "claude-haiku-4-5": Rates(1, 5, .10, 1.25, 2),
    "claude-fable-5-1": Rates(10, 50, .25, 12.50, 20),
    "gpt-6.1-sol": Rates(2, 10, .10, 2.50, long_context=True),
    "gpt-6-sol": Rates(2, 10, .20, 2.50, long_context=True),
    "gpt-6-astra": Rates(10, 50, 1, 12.50, long_context=True),
    "gpt-6-luna": Rates(.10, .50, .01, .125, long_context=True),
    "gpt-5.6-sol": Rates(4, 20, .40, 5, long_context=True),
    "gpt-5.3-codex": Rates(1.75, 14, .175, 0),
}


def count(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def model_rates(model: str):
    model = model.removeprefix("anthropic/").removeprefix("openai/")
    return RATES.get(re.sub(r"-\d{8}$", "", model))


def estimate(provider: str, model: str, usage: dict, context_input=None):
    """Return (USD or None, explanation). Reasoning is already in output tokens."""
    model = model if isinstance(model, str) else ""
    rates = model_rates(model)
    if rates is None:
        return None, "Pricing unavailable for " + (model or "unreported model")
    incoming, outgoing = count(usage.get("input_tokens")), count(usage.get("output_tokens"))
    if incoming is None or outgoing is None:
        return None, "Input/output token breakdown not reported"
    notes = []
    if provider == "claude":
        read = count(usage.get("cache_read_input_tokens", 0))
        writes = count(usage.get("cache_creation_input_tokens", 0))
        split = usage.get("cache_creation")
        split = split if isinstance(split, dict) else {}
        hour = count(split.get("ephemeral_1h_input_tokens", 0))
        short = count(split.get("ephemeral_5m_input_tokens", writes))
        if None in (read, writes, hour, short) or hour + short != writes:
            return None, "Invalid cache token breakdown"
        if writes and not split:
            notes.append("5m cache writes assumed")
        context = incoming + read + writes
        cost = incoming * rates.input + outgoing * rates.output + read * rates.read + short * rates.write + hour * rates.write_hour
    else:
        read = count(usage.get("cached_input_tokens", 0))
        writes = count(usage.get("cache_write_input_tokens", 0))
        if None in (read, writes) or read + writes > incoming or (writes and not rates.write):
            return None, "Invalid or unsupported cache token breakdown"
        context = count(context_input) if context_input is not None else incoming
        long = rates.long_context and context is not None and context > 272_000
        input_multiplier, output_multiplier = (2, 1.5) if long else (1, 1)
        cost = ((incoming - read - writes) * rates.input + read * rates.read + writes * rates.write) * input_multiplier + outgoing * rates.output * output_multiplier
        if long:
            notes.append("long-context rates")
        else:
            notes.append("Standard API token rates")
    # Use reported Claude speed/geography. Codex logs generally omit the tier;
    # their estimate is explicitly Standard API-equivalent pricing.
    multiplier = 1
    if provider == "claude":
        if usage.get("speed") == "fast":
            if model.removeprefix("anthropic/") not in ("claude-opus-5-5", "claude-opus-5", "claude-opus-4-8"):
                return None, "Fast-mode pricing unavailable for model"
            multiplier *= 2
            notes.append("Fast mode")
        if usage.get("inference_geo") == "us":
            multiplier *= 1.1
            notes.append("US inference")
    return cost * multiplier / 1_000_000, "; ".join(notes) or "Standard API token rates"
