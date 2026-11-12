"""Freshness and liquidity checks for exact-expiry option snapshots."""
from __future__ import annotations

from iv_decision import iv_value
from live_engine import model_exposure
from option_payoff import quoted_legs
from terminal_data import feed_has_delay, numeric, positive, time_or_none

MAX_QUOTE_AGE = 120
MAX_RELATIVE_SPREAD = .35


def quote_age(stamp, now):
    observed = time_or_none(stamp)
    return (now - observed).total_seconds() if observed else None


def age_text(seconds):
    if seconds is None or seconds < 0:
        return "—"
    return f"{int(seconds)}s" if seconds < 60 else f"{seconds / 60:.1f}m"


def spread_fraction(row):
    bid, ask = positive(row.get("bid")), positive(row.get("ask"))
    return (ask - bid) / ((ask + bid) / 2) if bid and ask and ask >= bid else None


def option_rows(feed, symbol, expiry):
    if not feed.snapshot:
        return []
    meta = feed.snapshot.meta
    if meta.get("symbol") not in (None, symbol) or expiry and meta.get("expiry") not in (None, expiry):
        return []
    return [row for row in feed.rows if row.get("symbol") in (None, symbol)
            and (expiry is None or row.get("expiry") == expiry)]


def quote_status(row, feed, now, *, max_age=MAX_QUOTE_AGE, paused=False):
    """A receipt timestamp can never make an old bid or ask fresh."""
    if paused:
        return "Paused"
    if not feed.snapshot or feed.error:
        return "Waiting for quotes" if feed.loading else "Quote feed unavailable"
    if feed.snapshot.meta.get("sandbox"):
        return "Sandbox data"
    if feed_has_delay(feed) or (numeric(row.get("delay_minutes")) or 0) > 0:
        return "Delayed quote"
    expires = time_or_none(row.get("expiry_utc"))
    if expires and expires <= now:
        return "Expired contract"
    age = quote_age(row.get("updated_at"), now)
    if age is None:
        return "No quote timestamp"
    if not 0 <= age <= max_age:
        return "Stale quote"
    width = spread_fraction(row)
    if width is None:
        return "No usable bid and ask"
    if width > MAX_RELATIVE_SPREAD:
        return "Wide spread"
    return "Fresh quote"


def fresh_option_rows(feed, symbol, expiry, now, *, max_age=MAX_QUOTE_AGE, paused=False):
    return [row for row in option_rows(feed, symbol, expiry)
            if quote_status(row, feed, now, max_age=max_age, paused=paused) == "Fresh quote"]


def option_review(feed, source, symbol, expiry, spot, direction, now, *, max_age=MAX_QUOTE_AGE, paused=False):
    right = "call" if direction > 0 else "put"
    base = {"source": source, "expiry": expiry, "right": right if direction else None, "ready": False}
    if not expiry or not direction or not spot:
        return {**base, "status": "Choose an expiration" if not expiry else "Waiting for direction and price"}
    rows = [row for row in option_rows(feed, symbol, expiry)
            if row.get("type") == right and positive(row.get("strike")) and row.get("contract")]
    if not rows:
        status = "Waiting for quotes" if feed.loading else "No matching contract"
        return {**base, "status": str(feed.error) if feed.error else status}
    # Inspect the nearest contract. A stale ATM quote must not silently select a distant strike.
    row = min(rows, key=lambda item: (abs(positive(item["strike"]) - spot), str(item["contract"])))
    status = quote_status(row, feed, now, max_age=max_age, paused=paused)
    leg = None
    if status == "Fresh quote":
        legs = quoted_legs([row], symbol, expiry, now)
        if legs:
            leg = legs[0]
        else:
            status = "Waiting for contract IV or settlement details"
    return {**base, "contract": row["contract"], "strike": positive(row["strike"]),
            "bid": positive(row.get("bid")), "ask": positive(row.get("ask")), "spread": spread_fraction(row),
            "iv": iv_value(row.get("iv")), "delta": numeric(row.get("delta")), "gamma": numeric(row.get("gamma")),
            "quote_at": row.get("updated_at"), "status": status, "ready": leg is not None}


def atm_iv_sample(feed, symbol, expiry, spot, now, *, max_age=MAX_QUOTE_AGE, paused=False):
    if not expiry or not spot:
        return None
    pairs = {}
    for row in fresh_option_rows(feed, symbol, expiry, now, max_age=max_age, paused=paused):
        strike, iv = positive(row.get("strike")), iv_value(row.get("iv"))
        if strike and iv and row.get("type") in ("call", "put"):
            pairs.setdefault(strike, {})[row["type"]] = row
    if not pairs:
        return None
    strike = min(pairs, key=lambda value: (abs(value - spot), value))
    call, put = (pairs[strike].get(right) for right in ("call", "put"))
    if not call or not put:
        return None
    stamps = [time_or_none(row["updated_at"]) for row in (call, put)]
    if abs((stamps[0] - stamps[1]).total_seconds()) > 30:
        return None
    return {"value": (iv_value(call["iv"]) + iv_value(put["iv"])) / 2,
            "as_of": min(stamps).isoformat(), "identity": f"{call['contract']} / {put['contract']}",
            "strike": strike, "source": "Webull ATM estimate"}


def sample_exposure(feed, symbol, expiry, spot, now, settings, *, paused=False):
    """Compare a bounded Webull sample using its own scope and OI assumptions."""
    if not expiry or not spot or not feed.snapshot:
        return {}
    contracts, times = [], []
    for row in fresh_option_rows(feed, symbol, expiry, now, max_age=settings.option_max_age, paused=paused):
        iv, oi = iv_value(row.get("iv")), numeric(row.get("open_interest"))
        if iv is None or oi is None or oi < 0:
            continue
        contracts.append({"expiry_utc": row["expiry_utc"], "strike": row["strike"], "right": row["type"][0].upper(),
                          "open_interest": oi, "iv_decimal": iv, "multiplier": row["multiplier"]})
        times.append(time_or_none(row["updated_at"]))
    strikes = [row["strike"] for row in contracts]
    if (len(contracts) < 10 or {row["right"] for row in contracts} != {"C", "P"}
            or not min(strikes) < spot < max(strikes) or (max(times) - min(times)).total_seconds() > 60):
        return {}
    coverage = {"scope": "Exact-expiry quoted sample", "expiry": expiry, "root": feed.snapshot.meta.get("root"),
                "contracts_modeled": len(contracts), "contracts_discovered": feed.snapshot.meta.get("discovered"),
                "lowest_strike": min(strikes), "highest_strike": max(strikes),
                "oldest_option_quote_utc": min(times).isoformat(), "oi_timestamp_verified": False}
    return model_exposure(contracts, coverage, symbol, spot, settings, now=now)
