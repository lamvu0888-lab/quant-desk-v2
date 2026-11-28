"""Fixed, outcome-blind contract selection experiments for offline replay."""
from dataclasses import dataclass

from terminal_data import numeric


FEATURES = ("expected_net", "target_before_stop", "downside", "liquidity", "sensitivity", "carry", "iv_risk")
BASE = (.25, .20, .15, .15, .10, .10, .05)
LEGACY_TILTS = {
    "base": BASE,
    "execution": (.20, .15, .15, .25, .10, .10, .05),
    "downside": (.20, .15, .25, .15, .10, .10, .05),
    "carry": (.20, .15, .15, .15, .10, .15, .10),
}


@dataclass(frozen=True)
class Band:
    field: str
    low: float
    high: float
    explanation: str


# Sequence order is the public numeric feedback parameter. Append new IDs at the end.
FILTERS = {
    "same_day": Band("dte", 0, 0, "same-day expirations only"),
    "short_dated": Band("dte", 1, 7, "1–7 calendar days to expiration only"),
    "balanced_delta": Band("abs_delta", .45, .65, "absolute Delta 0.45–0.65"),
    "bounded_gamma": Band("gamma_10bp", .01, .10, "modeled Delta change 0.01–0.10 per underlying move of 0.1%"),
    "provider_mid_iv": Band("iv_gap", 0., .03, "provider vs midpoint-implied IV gap at most 3 volatility points"),
    "tight_execution": Band("spread", 0., .08, "spread at most 8% of midpoint and displayed ask size covers quantity"),
}
TILTS = {**LEGACY_TILTS, **{name: BASE for name in FILTERS}}
PROFILE_GUIDE = ", ".join(
    f"{i}={FILTERS[name].explanation if name in FILTERS else name}"
    for i, name in enumerate(TILTS)
)


def _value(value, low, high=None):
    number = numeric(value)
    if number is None or number < low or (high is not None and number > high):
        return None
    return number


def _count(value):
    number = _value(value, 0.)
    return int(number) if number is not None and number.is_integer() else None


def profile_inputs(candidate, recommendation):
    """Read only origin-known data. A missing provider or quote field stays missing."""
    spot = _value(recommendation.get("spot"), 0.)
    gamma = _value(candidate.get("gamma"), 0.)
    delta = _value(candidate.get("delta"), -1., 1.)
    chain_iv = _value(candidate.get("provider_iv"), 0.)
    fitted_iv = _value(candidate.get("midpoint_iv"), 0.)
    ask = _value(candidate.get("ask"), 0.)
    bid = _value(candidate.get("bid"), 0.)
    return {
        "abs_delta": abs(delta) if delta is not None else None,
        "gamma_10bp": gamma * (spot * .001) if gamma is not None and spot else None,
        "iv_gap": abs(chain_iv - fitted_iv) if chain_iv and fitted_iv else None,
        "spread": (ask - bid) / ((ask + bid) / 2) if bid and ask and bid <= ask else None,
        "ask_size": _count(candidate.get("ask_size")),
        "quantity": _count(candidate.get("quantity")),
    }


def _matches(name, row):
    criteria = FILTERS[name]
    inputs = row.get("profile_inputs") or {}
    if criteria.field == "dte":
        value = _count(row.get("dte"))
    else:
        value = _value(inputs.get(criteria.field), 0.)
    if value is None:
        return None
    if criteria.field == "spread":
        size, qty = _count(inputs.get("ask_size")), _count(inputs.get("quantity"))
        if size is None or not qty:
            return None
        if size < qty:
            return False
    # Most stored numeric inputs originate in floating point arithmetic.
    return criteria.low - 1e-12 <= value <= criteria.high + 1e-12


def pick(trial, profile):
    """None means unknown. An explicit no-trade is a measured zero-dollar alternative."""
    if profile not in TILTS or not trial.get("rows"):
        return None
    ranked = []
    for row in trial["rows"]:
        vector = [_value((row.get("features") or {}).get(name), 0., 1.) for name in FEATURES]
        if any(v is None for v in vector):
            return None
        eligible = _matches(profile, row) if profile in FILTERS else True
        if eligible is None:
            # A missing measurement could change the winner. Do not silently discard that series.
            return None
        if eligible:
            score = sum(w * v for w, v in zip(TILTS[profile], vector))
            ranked.append((score, row))
    if not ranked:
        return {"code": None, "position_net": 0., "research_abstention": True}
    return min(ranked, key=lambda item: (-item[0], item[1]["code"]))[1]
