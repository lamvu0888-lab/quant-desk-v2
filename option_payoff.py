"""Quote-anchored option valuation for paper scenarios and execution costs."""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from scipy.special import ndtr

from data_utils import DataError
from desk_analytics import MINUTE
from iv_decision import MINUTES_PER_YEAR, iv_value
from market_session import ET, rth_bounds
from terminal_data import positive, time_or_none, valid_date


@dataclass(frozen=True)
class OptionLeg:
    contract: str
    right: str
    strike: float
    bid: float
    ask: float
    iv: float
    multiplier: float
    expiry_at: str


@dataclass(frozen=True)
class OptionPosition:
    legs: tuple
    label: str
    asof: str
    fee: float = .65
    slippage: float = .02
    rate: float = .04
    dividend: float = 0.
    iv_shock_points: float = 0.


def quoted_legs(rows, symbol, expiry, asof, *, session_time=None):
    """Reject adjusted/unknown contracts, timestamp-less quotes and ambiguous expiries."""
    day = valid_date(expiry)
    bounds = rth_bounds(day) if day else None
    if not bounds:
        return []
    accepted = {}
    for row in rows:
        if row.get("symbol") not in (None, symbol) or row.get("expiry") != expiry or row.get("standard") is False:
            continue
        contract = str(row.get("contract") or "").replace(" ", "").upper()
        match = re.fullmatch(r"([A-Z][A-Z0-9.]{0,11})(\d{6})([CP])(\d{8})", contract)
        right = row.get("type")
        strike, bid, ask = (positive(row.get(key)) for key in ("strike", "bid", "ask"))
        iv, stamp = iv_value(row.get("iv")), time_or_none(row.get("updated_at"))
        if (not match or not strike or not bid or not ask or not iv or stamp is None
                or right not in ("call", "put") or right[0].upper() != match[3] or expiry[2:].replace("-", "") != match[2]
                or abs(strike - int(match[4]) / 1000) > .001 or ask < bid or (ask - bid) / ((ask + bid) / 2) > .35):
            continue
        if not 0 <= (asof - stamp).total_seconds() <= 120:
            continue
        root = match[1]
        if root not in ({symbol, "SPXW", "SPXO"} if symbol == "SPX" else {symbol}):
            continue
        explicit = time_or_none(row.get("expiry_utc") or row.get("expiration_time"))
        if explicit and explicit.astimezone(ET).date() != day:
            continue
        if root in ("SPX", "SPXO") and (explicit is None or asof.astimezone(ET).date() >= day):
            continue
        # PM-dated standard series use the regular close as the model clock
        # when the chain omits a settlement timestamp.
        expires = explicit or bounds[1]
        if expires <= (session_time or asof):
            continue
        multiplier = positive(row.get("multiplier")) if row.get("multiplier") is not None else 100.
        if multiplier != 100:
            continue
        accepted[contract] = OptionLeg(contract, right, strike, bid, ask, iv, multiplier, expires.isoformat())
    return sorted(accepted.values(), key=lambda leg: (leg.right, leg.strike, leg.contract))


def price_option(spot, strike, years, iv, right, rate=.04, dividend=0.):
    """Dividend-adjusted European mark, with exact intrinsic at expiry."""
    safe = np.maximum(years, 1e-12)
    vol = np.maximum(iv, .0001)
    root = np.sqrt(safe)
    d1 = (np.log(spot / strike) + (rate - dividend + .5 * vol ** 2) * safe) / (vol * root)
    d2 = d1 - vol * root
    a, b = spot * np.exp(-dividend * safe), strike * np.exp(-rate * safe)
    if right == "call":
        return np.where(years > 0, np.maximum(0., a * ndtr(d1) - b * ndtr(d2)), np.maximum(spot - strike, 0.))
    return np.where(years > 0, np.maximum(0., b * ndtr(-d2) - a * ndtr(-d1)), np.maximum(strike - spot, 0.))


def create_position(structure, direction, long, short, asof, hold, *, fee, slippage, rate=.04, dividend=0.,
                    iv_shock_points=0.):
    if direction not in (-1, 1) or long is None:
        raise DataError("Choose the structure and its long leg.")
    if structure not in ("Long option", "Debit vertical", "Credit vertical"):
        raise DataError("Choose a defined-risk structure.")
    right = ("call" if direction > 0 else "put") if structure != "Credit vertical" else ("put" if direction > 0 else "call")
    if long.right != right:
        raise DataError("This option right conflicts with the plan direction and structure.")
    if structure == "Long option":
        legs = ((1, long),)
    else:
        if (not short or long.right != short.right or long.expiry_at != short.expiry_at
                or long.multiplier != short.multiplier):
            raise DataError("Choose two legs with the same right, settlement time and multiplier.")
        spread = (short.strike - long.strike) * (1 if long.right == "call" else -1)
        if (structure == "Debit vertical" and spread <= 0) or (structure == "Credit vertical" and spread >= 0):
            raise DataError("The strike order does not match the selected debit or credit vertical.")
        legs = ((1, long), (-1, short))
    first = asof.replace(second=0, microsecond=0)
    if first < asof:
        first += MINUTE
    seconds_left = (time_or_none(long.expiry_at) - first).total_seconds()
    if seconds_left <= hold * 60:
        raise DataError("Select a holding period that ends before the option's modeled settlement.")
    position = OptionPosition(legs, structure, asof.isoformat(), fee, slippage, rate, dividend, iv_shock_points)
    risk = position_risk(position)
    if not risk["valid"]:
        raise DataError("Executable entry prices and costs do not produce a viable debit or credit structure.")
    return position


def position_risk(position, *, debit=None, executable_validity=False):
    """Return initial defined risk from the quoted fills and contract payoff."""
    multiplier = position.legs[0][1].multiplier
    if debit is None:
        debit = sum((leg.ask + position.slippage) if qty > 0 else -max(0., leg.bid - position.slippage)
                    for qty, leg in position.legs)
    fees = len(position.legs) * 2 * position.fee
    if len(position.legs) == 1:
        valid, risk = debit > 0, debit * multiplier + fees
    else:
        a, b = (leg for _, leg in position.legs)
        width = abs(a.strike - b.strike)
        credit = position.label == "Credit vertical"
        valid = ((debit < 0) & (debit > -width) & (-debit * multiplier > fees)
                 if credit else (debit > 0) & (debit < width) & ((width - debit) * multiplier > fees))
        risk = (debit + (width if credit else 0)) * multiplier + fees
    if executable_validity:
        return valid, risk
    return {"valid": bool(valid), "max_loss": float(risk), "entry_cash": float(-debit * multiplier),
            "round_trip_fees": fees}


def option_path_pnl(position, paths, result, selected):
    """Reprice every leg at modeled fill/exit, with the correct side of its spread."""
    close, returns = paths["close"][selected], paths["returns"][selected]
    fills, exits = result["fill"][selected], result["exit"][selected]
    row = np.arange(len(fills))
    start = time_or_none(position.asof)
    floor = start.replace(second=0, microsecond=0)
    offset = ((floor + MINUTE - start).total_seconds() / 60) if start > floor else 0.
    base_spot = float(close[0, 0])
    entry_spot, exit_spot = close[row, fills], result["exit_price"][selected]
    # A bounded spot/IV relationship and changing realized-vol scale are
    # explicit scenario assumptions, not a forecast of the future IV surface.
    seed_scale = max(1e-8, float(np.std(paths["returns"][:, 0])))
    ewma = np.full(len(row), seed_scale ** 2)
    relative = np.zeros_like(close)
    for t in range(returns.shape[1]):
        ewma = .88 * ewma + .12 * returns[:, t] ** 2
        relative[:, t + 1] = np.clip(np.sqrt(ewma) / seed_scale - 1, -.75, 2.)
    debit = np.zeros(len(row))
    credit = np.zeros(len(row))
    for qty, leg in position.legs:
        remaining = (time_or_none(leg.expiry_at) - start).total_seconds() / 60
        mid = (leg.ask + leg.bid) / 2
        half_spread = (leg.ask - leg.bid) / 2
        fair_at_origin = price_option(base_spot, leg.strike, remaining / MINUTES_PER_YEAR, leg.iv, leg.right,
                                      position.rate, position.dividend)
        residual = mid - fair_at_origin

        def mark(spot, elapsed):
            clock = elapsed + np.where(elapsed > 0, offset, 0.)
            adjust = (-.40 * np.log(spot / base_spot) + .12 * leg.iv * relative[row, elapsed]
                      + position.iv_shock_points / 100 * np.minimum(1, clock / 5))
            vol = np.clip(leg.iv + adjust, .0001, 5.)
            minutes = np.maximum(0., remaining - clock)
            fair = price_option(spot, leg.strike, minutes / MINUTES_PER_YEAR, vol, leg.right, position.rate, position.dividend)
            anchored = np.maximum(0., fair + residual * np.sqrt(minutes / remaining))
            spread = half_spread * (1 + .25 * np.abs(relative[row, elapsed]))
            return anchored, spread

        opening, opening_spread = mark(entry_spot, fills)
        closing, closing_spread = mark(exit_spot, exits)
        debit += qty * np.maximum(0., opening + qty * (opening_spread + position.slippage))
        credit += qty * np.maximum(0., closing - qty * (closing_spread + position.slippage))
    valid, risk = position_risk(position, debit=debit, executable_validity=True)
    pnl = (credit - debit) * position.legs[0][1].multiplier - 2 * len(position.legs) * position.fee
    return pnl, risk, valid, position_risk(position)
