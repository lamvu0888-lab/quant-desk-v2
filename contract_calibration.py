"""Quote-time IV checks shared by the option charts and contract research."""
from dataclasses import dataclass
from functools import lru_cache

from iv_decision import iv_value
from option_quotes import MAX_RELATIVE_SPREAD, spread_fraction
from terminal_data import positive, time_or_none
from v3_options import YEAR, calibrated_iv, value

# Rounded minute-feed snapshots can be individually fresh while several ticks apart.
# This tolerance is an engineering matching rule; it does not imply execution simultaneity.
MAX_CLOCK_GAP = 30.


@dataclass(frozen=True)
class QuoteCalibration:
    iv: float | None
    basis: str
    midpoint_iv: float | None
    provider_iv: float | None
    provider_fair: float | None
    age_gap: float | None
    synchronized: bool


@lru_cache(maxsize=4096)
def _fit(spec, spot, opt_time, bid, ask, provider, rate, dividend):
    t = (spec.expires - opt_time).total_seconds() / YEAR
    fair = None
    if provider:
        try:
            fair = float(value(spec, spot, t, provider, rate, dividend))
        except ValueError:
            pass
    fitted = calibrated_iv(spec, spot, opt_time, (ask + bid) / 2, rate, dividend)
    return fair, fitted


def calibrate_quote(spec, row, spot, spot_at, now, rate, dividend, max_quote_age=90):
    """Compare like-for-like option and underlying observations at the quote time.

    A consistent provider IV is retained. Otherwise invert the two-sided midpoint,
    provided the quote source clocks, expiry, model bounds and spread support it.
    """
    provider = iv_value(row.get("iv"))
    opt_time = time_or_none(row.get("updated_at"))
    spot_time = time_or_none(spot_at)
    gap = abs((opt_time - spot_time).total_seconds()) if opt_time and spot_time else None
    synchronized = (gap is not None and gap <= MAX_CLOCK_GAP and
                    all(0 <= (now - at).total_seconds() <= max_quote_age for at in (opt_time, spot_time)))
    width = spread_fraction(row)
    bid, ask = positive(row.get("bid")), positive(row.get("ask"))
    if not (synchronized and positive(spot) and width is not None and width <= MAX_RELATIVE_SPREAD
            and opt_time < min(spec.last_trade, spec.expires) and bid and ask):
        return QuoteCalibration(None, "Waiting for synchronized, two-sided quotes", None, provider, None, gap, False)
    # Freshness is checked on every call; only identical market/model inputs reuse the IV inversion.
    fair, fit = _fit(spec, spot, opt_time, bid, ask, provider, rate, dividend)
    if fair is not None and bid - .005 <= fair <= ask + .005:
        return QuoteCalibration(provider, "Chain IV prices inside the spread", fit, provider, fair, gap, True)
    if fit is not None:
        return QuoteCalibration(fit, "Fit to synchronized midpoint", fit, provider, fair, gap, True)
    return QuoteCalibration(None, "Midpoint outside the supported model range", fit, provider, fair, gap, True)
