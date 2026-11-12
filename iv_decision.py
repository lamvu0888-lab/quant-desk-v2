"""Expiration-aware IV readings and conditional structure guidance."""
from __future__ import annotations

from math import sqrt

from market_session import ET, rth_bounds
from terminal_data import numeric, positive, valid_date

MINUTES_PER_YEAR = 365.25 * 24 * 60


def iv_value(value):
    iv = positive(value)
    return iv if iv is not None and iv <= 5 else None


def fraction(value):
    val = numeric(value)
    return val if val is not None and 0 <= val <= 1 else None


def iv_read(surface, expiry, now, spot, regime, *, usable, atm_sample=None):
    """Use exact expiry IV when requested. The provider's rank is a 30D measure."""
    obj = surface if usable else {}
    term = obj.get("term") if isinstance(obj.get("term"), list) else []
    skew = obj.get("skew") if isinstance(obj.get("skew"), list) else []
    term_by_expiry = {row["expiry"]: iv for row in term if isinstance(row, dict)
                      and valid_date(row.get("expiry")) and (iv := iv_value(row.get("atm_iv"))) is not None}
    atm30 = iv_value(obj.get("atm_iv_30d"))
    atm = atm_sample["value"] if expiry and atm_sample else term_by_expiry.get(expiry) if expiry else atm30
    rank30, pct30 = fraction(obj.get("iv_rank")), fraction(obj.get("iv_percentile"))
    rv = iv_value(obj.get("realized_vol_20d"))
    ratio = atm / rv if atm is not None and rv is not None else None
    regime_label = regime.get("label", "Waiting for bars")
    if ratio is None:
        valuation = "Unavailable"
    elif ratio >= 1.20:
        valuation = "Rich vs trailing RV"
    elif ratio <= .85:
        valuation = "Low vs trailing RV"
    else:
        valuation = "Near trailing RV"
    skew_row = next((row for row in skew if isinstance(row, dict) and row.get("expiry") == expiry), {})
    put, call = iv_value(skew_row.get("put_25d_iv")), iv_value(skew_row.get("call_25d_iv"))
    provider = obj.get("expected_move") if isinstance(obj.get("expected_move"), dict) else {}
    anchor, width = positive(obj.get("underlying_price")), positive(provider.get("move_usd"))
    provider_expiry = provider.get("expiry")
    today = now.astimezone(ET).date()
    expiry_day = valid_date(expiry)
    bounds = rth_bounds(expiry_day) if expiry_day is not None and expiry_day >= today else None
    minutes = (bounds[1] - now).total_seconds() / 60 if bounds else None
    reference = spot * atm * sqrt(minutes / MINUTES_PER_YEAR) if spot and atm and minutes and minutes > 0 else None
    # A band recentered on the current price cannot measure remaining headroom.
    band = None
    if expiry and expiry == provider_expiry and minutes and minutes > 0 and anchor and width and anchor > width:
        band = (anchor - width, anchor + width)
    remaining_up = max(0, band[1] - spot) if band and spot else None
    remaining_down = max(0, spot - band[0]) if band and spot else None
    term_ordered = sorted((day, iv) for day, iv in term_by_expiry.items() if day >= str(today))
    term_shape = None
    if len(term_ordered) >= 2:
        gap = term_ordered[-1][1] - term_ordered[0][1]
        term_shape = "Back months higher" if gap > .005 else "Front months higher" if gap < -.005 else "Broadly flat"
    if ratio is None or regime_label == "Waiting for bars":
        guidance = "Wait for IV, realized volatility and completed price bars before comparing structures."
    elif ratio <= .85 and regime_label == "Expanding":
        guidance = "For a confirmed direction, compare a long call or put with a debit spread after costs."
    elif ratio >= 1.20 and regime_label == "Compressing":
        guidance = "A defined-risk credit spread is a scenario to test. Check short-strike distance, event risk and max loss."
    elif ratio >= 1.20 or regime_label == "Compressing":
        guidance = "A debit spread can reduce the premium paid. Wait for directional confirmation and compare its capped payoff."
    else:
        guidance = "Compare a long option and a debit spread if direction confirms. Stand aside when costs or structure weaken the plan."
    return {"expiry": expiry, "atm": atm, "atm30": atm30, "rank30": rank30, "percentile30": pct30, "rv": rv,
            "atm_source": atm_sample["source"] if expiry and atm_sample else "Tradytics",
            "atm_sample": atm_sample,
            "ratio": ratio, "valuation": valuation, "regime": regime_label, "term": term_ordered,
            "term_shape": term_shape, "put_skew": put, "call_skew": call,
            "skew_points": 100 * (put - call) if put is not None and call is not None else None,
            "reported_move": width if not expiry or band else None,
            "reported_expiry": provider_expiry if not expiry or band else None,
            "reference_move": reference, "band": band, "remaining_up": remaining_up,
            "remaining_down": remaining_down, "guidance": guidance, "surface_usable": usable}
