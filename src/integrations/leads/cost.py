"""Cost conversion: ElevenLabs credits → USD.

ElevenLabs returns `metadata.cost` as integer credits (sum of `llm_charge`
and `call_charge`). We don't get a single `cost_usd` field back, so we
compute it ourselves at the API/UI boundary.

Empirically from a real call: llm_charge=596 credits cost $0.05958255 USD
(visible in metadata.charging.llm_price). That's ≈ 10000 credits per USD,
which lines up with ElevenLabs's pricing pages.

Override via env: CREDITS_PER_USD=10000.
"""

import os


def credits_per_usd() -> float:
    raw = os.getenv("CREDITS_PER_USD", "10000")
    try:
        v = float(raw)
        return v if v > 0 else 10000.0
    except ValueError:
        return 10000.0


def credits_to_usd(credits: int | float | None) -> float:
    if credits is None:
        return 0.0
    try:
        return round(float(credits) / credits_per_usd(), 4)
    except (TypeError, ValueError):
        return 0.0


def fmt_usd(usd: float | int | None) -> str:
    if usd is None:
        return "$0.00"
    return f"${float(usd):.2f}"
