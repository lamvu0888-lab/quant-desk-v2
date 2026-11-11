"""First-order dealer hedge scenarios from a coherent provider snapshot."""
from __future__ import annotations

from math import sqrt

from terminal_data import numeric, positive, time_or_none

HORIZONS = (5, 10, 15, 30)
VALIDATION_HORIZONS = (5, 10, 15)
UNITS = {"gamma": "usd_per_1pct_move", "vanna": "usd_per_vol_point",
         "charm": "shares_per_day", "delta": "shares"}


def hedge_snapshot(objects, now, *, preview=False):
    """The vendor totals already incorporate the vendor's contract sizing."""
    gamma = objects.get("gamma") or {}
    spot, stamp = positive(gamma.get("spot")), time_or_none(gamma.get("time"))
    if not spot or stamp is None or (not preview and not 0 <= (now - stamp).total_seconds() <= 420):
        return None

    def coherent(greek):
        obj = objects.get(greek) or {}
        other_spot, other_stamp = positive(obj.get("spot")), time_or_none(obj.get("time"))
        if (obj.get("unit") != UNITS[greek] or other_stamp is None or other_spot is None
                or abs(other_spot / spot - 1) > .005 or abs((stamp - other_stamp).total_seconds()) > 120
                or (not preview and not 0 <= (now - other_stamp).total_seconds() <= 420)
                or (gamma.get("model") and obj.get("model") and gamma["model"] != obj["model"])):
            return None
        value = numeric(obj.get("total"))
        if value is None:
            return None
        return value, other_spot

    g, v, c, d = (coherent(greek) for greek in ("gamma", "vanna", "charm", "delta"))
    if g is None:
        return None
    gex = g[0]
    levels = gamma.get("levels") if isinstance(gamma.get("levels"), dict) else {}
    profile = [(strike, net) for row in (gamma.get("rows") or []) if isinstance(row, dict)
               and (strike := positive(row.get("strike"))) and abs(strike / spot - 1) <= .025
               and (net := numeric(row.get("net"))) is not None]
    near = sorted(profile, key=lambda row: abs(row[1]), reverse=True)[:8]
    return {"spot": spot, "stamp": stamp.isoformat(), "gex": gex, "gamma_shares_per_point": gex / (.01 * spot * spot),
            "vanna_shares_per_iv_point": v[0] / v[1] if v else None,
            "charm_shares_per_day": c[0] if c else None, "delta_shares": d[0] if d else None,
            "complete": v is not None and c is not None, "model": gamma.get("model"),
            "levels": {key: positive(levels.get(key)) for key in ("flip", "call_wall", "put_wall")},
            "strikes": [{"strike": strike, "gex": net, "delta_change_per_point": net / (.01 * spot * spot)}
                        for strike, net in near]}


def hedge_change(snapshot, move, iv_points, minutes, *, base_price=None):
    """Hedge is the negative change in modeled option-book delta."""
    gamma = -snapshot["gamma_shares_per_point"] * move
    vanna = snapshot["vanna_shares_per_iv_point"]
    charm = snapshot["charm_shares_per_day"]
    vanna = -vanna * iv_points if vanna is not None else None
    charm = -charm * minutes / (24 * 60) if charm is not None else None
    total = gamma + vanna + charm if vanna is not None and charm is not None else None
    return {"gamma": gamma, "vanna": vanna, "charm": charm, "total": total,
            "notional": total * (base_price or snapshot["spot"]) if total is not None else None}


def hedge_scenarios(snapshot, spot, atr, minutes_left, *, iv_shock=1.0):
    if not snapshot or not spot or not atr or abs(spot / snapshot["spot"] - 1) > .0075:
        return []
    rows = []
    for minutes in HORIZONS:
        if minutes > minutes_left:
            continue
        # ATR is a 5m high-low range scale. The scenarios are symmetric stress
        # cases with square-root scaling, not distribution quantiles.
        width = atr * sqrt(minutes / 5)
        for scenario, move, iv in (("Down / IV up", -width, iv_shock), ("Flat / IV flat", 0, 0),
                                   ("Up / IV down", width, -iv_shock)):
            rows.append({"horizon": minutes, "scenario": scenario, "move": move, "price": spot + move, "iv_points": iv,
                         **hedge_change(snapshot, move, iv, minutes, base_price=spot)})
    return rows


def level_crossings(prior_close, close, levels):
    if prior_close is None or close is None:
        return []
    names = {"flip": "Gamma flip", "call_wall": "Call wall", "put_wall": "Put wall"}
    return [{"level": names[key], "price": level, "direction": "above" if close >= level else "below"}
            for key, level in levels.items() if level is not None and
            ((prior_close < level <= close) or (close <= level < prior_close))]


def price_response(spot, snapshot, outcome_price, minutes):
    """Condition the same local sensitivity on a later observed spot move, holding IV fixed."""
    move = outcome_price - spot
    return {"observed_price": outcome_price, "move": move, "return_bps": 10_000 * move / spot,
            "price_and_time_hedge_proxy": hedge_change(snapshot, move, 0, minutes, base_price=spot)["total"]}
