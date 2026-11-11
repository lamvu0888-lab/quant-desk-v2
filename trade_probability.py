"""Intraday path simulation with causal fitting and executable-entry assumptions."""
from __future__ import annotations

from datetime import timedelta

import numpy as np

from data_utils import DataError, utc
from desk_analytics import MINUTE
from market_session import ET, rth_bounds
from terminal_data import positive

MODEL_VERSION = "qd25-block-v1"
STRATEGIES = ("Immediate", "Breakout and retest", "EMA 8/10 pullback", "VWAP retest")
TRAINING_MINUTES = 120


def fitting_sample(candles, asof):
    """Use only consecutive, completed regular-session observations before origin."""
    rows = []
    previous = None
    session = None
    opened = closed = None
    for row in candles:
        stamp = utc(row["time_utc"])
        if stamp + MINUTE > asof or row.get("closed") is False:
            continue
        day = stamp.astimezone(ET).date()
        if day != session:
            bounds = rth_bounds(day)
            session, previous = day, None
            opened, closed = bounds or (None, None)
        if opened is None or not opened <= stamp < closed:
            continue
        base = previous[1] if previous and previous[0] + MINUTE == stamp else row["open"]
        rows.append((stamp, (stamp - opened).total_seconds() // 60, np.log(row["close"] / base),
                     min(0., np.log(row["low"] / base)), max(0., np.log(row["high"] / base))))
        previous = stamp, row["close"]
    if len(rows) < TRAINING_MINUTES:
        raise DataError(f"Need {TRAINING_MINUTES} completed one-minute bars for the model.")
    slots = np.array([row[1] for row in rows], dtype=int)
    values = np.array([row[2:] for row in rows])
    sigma = float(np.std(values[:, 0], ddof=1))
    if not 1e-8 < sigma < .15:
        raise DataError("Recent minute returns have an unusable volatility scale.")
    recent = values[-min(25, len(rows)):, 0]
    local = float(np.clip(np.sqrt(np.mean(recent ** 2)), .55 * sigma, 2 * sigma))
    block_size = 5
    valid = np.flatnonzero(np.all(np.lib.stride_tricks.sliding_window_view(np.diff(slots), block_size - 1) == 1, axis=1))
    # Offsets must also advance in actual time; holiday/session gaps are excluded.
    valid = np.array([i for i in valid if rows[i + block_size - 1][0] - rows[i][0] == 4 * MINUTE], dtype=int)
    if len(valid) < 30:
        raise DataError("Need at least 30 uninterrupted five-minute return blocks.")
    # Each historical block is scaled by volatility measured before its own start.
    lagged = np.array([np.sqrt(np.mean(values[max(0, i - 20):i, 0] ** 2)) if i else sigma
                       for i in range(len(values))])
    lagged = np.clip(lagged, .4 * sigma, 2.5 * sigma)
    seasonality = []
    for bucket in range(13):
        group = values[(slots // 30) == bucket, 0]
        seasonality.append(float(np.clip(np.std(group) / sigma, .65, 1.65)) if len(group) >= 25 else 1.)
    return {"slots": slots, "values": values, "sigma": sigma, "local": local, "starts": valid, "lagged": lagged,
            "seasonality": seasonality, "count": len(rows),
            "sessions": len({row[0].astimezone(ET).date() for row in rows})}


def simulate_paths(fit, spot, minutes, minute_of_day, *, n=2000, seed=805, model="block", regime=None,
                   flow_pressure=0., gamma_pressure=0., jump_fraction=0.):
    """Return one-minute close and high/low paths, including the origin."""
    rng = np.random.default_rng(seed)
    sigma = fit["sigma"]
    returns, low_extra, high_extra = (np.zeros((n, minutes)) for _ in range(3))
    if model == "gaussian":
        returns = rng.normal(0, sigma, size=(n, minutes))
        observed = fit["values"]
        lower_wick = np.maximum(0, np.minimum(observed[:, 0], 0) - observed[:, 1])
        upper_wick = np.maximum(0, observed[:, 2] - np.maximum(observed[:, 0], 0))
        low_extra = rng.choice(lower_wick, size=(n, minutes))
        high_extra = rng.choice(upper_wick, size=(n, minutes))
    else:
        starts = fit["starts"]
        regime_ratio = {"Compressing": .8, "Expanding": 1.35}.get(regime, 1.)
        local_ratio = fit["local"] / sigma
        wanted = np.clip((local_ratio + regime_ratio) / 2, .6, 1.8)
        scale_tilt = float(np.clip(1 + .08 * flow_pressure + .08 * gamma_pressure, .86, 1.14))
        variance = np.full(n, fit["local"] ** 2)
        for j in range(0, minutes, 5):
            width = min(5, minutes - j)
            distance = np.abs(fit["slots"][starts] - (minute_of_day + j))
            vol_distance = np.abs(np.log(fit["lagged"][starts] / (wanted * sigma)))
            weights = (.22 + .78 * np.exp(-distance / 55)) * (.22 + .78 * np.exp(-vol_distance / .5))
            base = rng.choice(starts, n, p=weights / weights.sum())
            samples = fit["values"][base[:, None] + np.arange(width)]
            local_scale = fit["lagged"][base]
            for k in range(width):
                t = j + k
                season = fit["seasonality"][min(12, (minute_of_day + t) // 30)]
                path_scale = np.clip(np.sqrt(variance), .5 * sigma, 2.5 * sigma)
                ratio = np.clip(scale_tilt * season * path_scale / local_scale, .4, 2.8)
                normalized = samples[:, k, :]
                # Shrink directional drift to zero within the sampled, weighted clock window.
                drift = float(np.average(fit["values"][starts + k, 0], weights=weights))
                returns[:, t] = (normalized[:, 0] - drift) * ratio
                low_extra[:, t] = (np.minimum(normalized[:, 0], 0) - normalized[:, 1]) * ratio
                high_extra[:, t] = (normalized[:, 2] - np.maximum(normalized[:, 0], 0)) * ratio
                variance = .94 * variance + .06 * returns[:, t] ** 2
    if jump_fraction:
        selected = rng.random(n) < jump_fraction
        where = rng.integers(0, minutes, size=n)
        rows = np.flatnonzero(selected)
        returns[rows, where[rows]] += rng.choice([-1, 1], len(rows)) * 3.5 * fit["local"]
    returns = np.clip(returns, -.20, .20)
    logs = np.concatenate((np.zeros((n, 1)), np.cumsum(returns, axis=1)), axis=1)
    lows = np.minimum(logs[:, :-1], logs[:, 1:]) - np.maximum(low_extra, 0)
    highs = np.maximum(logs[:, :-1], logs[:, 1:]) + np.maximum(high_extra, 0)
    return {"close": spot * np.exp(logs), "low": spot * np.exp(lows), "high": spot * np.exp(highs),
            "returns": returns}


def path_outcomes(paths, plan, strategy):
    """Evaluate a pending entry at a confirming close, then manage it from the next minute."""
    close, low, high = (paths[key] for key in ("close", "low", "high"))
    n, length = close.shape
    direction, stop, tp1, tp2 = (plan[key] for key in ("direction", "stop", "tp1", "tp2"))
    invalidation = plan["invalidation"]
    fill = np.full(n, -1, dtype=int)
    exit_index = np.full(n, -1, dtype=int)
    exit_price = np.full(n, np.nan)
    first = np.zeros(n, dtype=bool)
    state = np.full(n, "NO FILL", dtype="<U12")
    eligible = np.ones(n, dtype=bool)
    break_level = plan.get("range", (None, None))[1 if direction > 0 else 0]
    if plan.get("break_at") and plan.get("break_level") and plan.get("setup") in ("Breakout and retest", "Breakout close"):
        break_level = plan["break_level"]
    level = {"Breakout and retest": break_level, "EMA 8/10 pullback": plan.get("ema_level"),
             "VWAP retest": plan.get("vwap")}.get(strategy)
    if strategy != "Immediate" and level is None:
        return None
    if strategy == "Immediate":
        viable = (direction * (close[:, 0] - stop) > 0) & (direction * (tp1 - close[:, 0]) > 0)
        fill[viable] = 0
    broken = np.zeros(n, dtype=bool)
    if strategy == "Breakout and retest" and plan.get("break_at") and plan.get("break_direction") == direction:
        broken = direction * (close[:, 0] - level) > 0
    pad = max(.02 * abs(plan["entry"] - stop), close[0, 0] * .00003)
    for j in range(1, length):
        previous, current = close[:, j - 1], close[:, j]
        adverse, favorable = (low[:, j - 1], high[:, j - 1]) if direction > 0 else (high[:, j - 1], low[:, j - 1])
        active = (fill >= 0) & (exit_index < 0)
        stopped = active & (direction * (adverse - stop) <= 0)
        second = active & ~stopped & (direction * (favorable - tp2) >= 0)
        first |= active & ~stopped & (direction * (favorable - tp1) >= 0)
        invalid = active & ~stopped & ~second & (direction * (current - invalidation) < 0)
        timed = active & ~stopped & ~second & ~invalid & (j == length - 1)
        exit_mask = stopped | second | invalid | timed
        exit_index[exit_mask] = j
        opened = paths["open"][:, j - 1] if "open" in paths else previous
        exit_price[stopped] = np.where(direction * (opened[stopped] - stop) < 0, opened[stopped], stop)
        exit_price[second] = tp2
        exit_price[invalid | timed] = current[invalid | timed]
        state[stopped], state[second], state[invalid], state[timed] = "STOP", "TP2", "INVALIDATED", "TIME LIMIT"
        if strategy != "Immediate" and j < length - 1:
            eligible &= (direction * (adverse - stop) > 0) & (direction * (favorable - tp2) < 0)
            held = broken if strategy == "Breakout and retest" else direction * (previous - level) > 0
            test = (direction * (adverse - level) <= pad) & (direction * (current - level) > pad)
            order = (fill < 0) & eligible & held & test & (direction * (tp1 - current) > 0)
            fill[order] = j
            if strategy == "Breakout and retest":
                broken |= direction * (current - level) > pad
    return {"fill": fill, "exit": exit_index, "exit_price": exit_price, "tp1": first, "state": state}


def wilson(success, count):
    if not count:
        return None
    p = success / count
    z2 = 1.95996398454 ** 2
    center = (p + z2 / (2 * count)) / (1 + z2 / count)
    spread = np.sqrt(p * (1 - p) / count + z2 / (4 * count ** 2)) * np.sqrt(z2) / (1 + z2 / count)
    return max(0., float(center - spread)), min(1., float(center + spread))


def summarize_paths(paths, result, plan, *, option=None, cost_bps=1.0):
    fills, exits, state = (result[key] for key in ("fill", "exit", "state"))
    keep = fills >= 0
    ix = np.flatnonzero(keep)
    prices = paths["close"][ix, fills[keep]]
    risk = abs(prices - plan["stop"])
    pnl = (result["exit_price"][keep] - prices) * plan["direction"] - prices * cost_bps / 10_000
    option_detail = None
    if option is not None and len(ix):
        from option_payoff import option_path_pnl

        pnl, risk, valid, option_detail = option_path_pnl(option, paths, result, ix)
        keep[ix[~valid]] = False
        pnl, risk = pnl[valid], risk[valid]
    n, count = len(fills), int(keep.sum())
    stat = {"paths": n, "fills": count, "p_fill": count / n, "p_no_fill": 1 - count / n}
    if not count:
        return stat
    normalized = pnl / risk
    all_r = np.zeros(n)
    all_r[keep] = normalized
    all_pnl = np.zeros(n)
    all_pnl[keep] = pnl
    wins, losses = normalized[normalized > 0], normalized[normalized < 0]
    p1, p2, stop = int((keep & result["tp1"]).sum()), int((keep & (state == "TP2")).sum()), int((keep & (state == "STOP")).sum())
    stopped_first = int((keep & (state == "STOP") & ~result["tp1"]).sum())
    never = int((keep & ~result["tp1"] & (state != "STOP")).sum())
    ev_spread = float(1.96 * np.std(all_r, ddof=1) / np.sqrt(n)) if n > 1 else 0.
    ev = float(np.mean(all_r))
    stat.update(p_tp1=p1 / count, p_tp2=p2 / count, p_tp1_all=p1 / n, p_tp2_all=p2 / n,
                p_stop_first=stopped_first / count, p_stop_before_tp2=stop / count, p_neither=never / count,
                p_no_tp2_or_stop=(count - p2 - stop) / count,
                p_invalidated=float((keep & (state == "INVALIDATED")).sum() / count),
                p_profit=float(np.mean(normalized > 0)), tp1_interval=wilson(p1, count),
                tp2_interval=wilson(p2, count), expected_minutes=float(np.mean(exits[keep] - fills[keep])),
                ev_r=ev, ev_r_filled=float(np.mean(normalized)), ev_ci=(ev - ev_spread, ev + ev_spread),
                ev_pnl=float(np.mean(all_pnl)), p05_r=float(np.quantile(normalized, .05)),
                expected_shortfall_r=float(np.mean(np.sort(normalized)[:max(1, int(np.ceil(count * .05)))])),
                breakeven=float(-losses.mean() / (wins.mean() - losses.mean())) if len(wins) and len(losses) else None,
                reward_risk=float(-wins.mean() / losses.mean()) if len(wins) and len(losses) else None,
                option_detail=option_detail)
    return stat


def evaluate_plan(candles, plan, asof, *, n=2000, option=None, strategies=STRATEGIES, include_stress=True, seed=805):
    spot, hold = positive(plan.get("price")), plan.get("hold", 0)
    if (not spot or plan.get("direction") not in (-1, 1) or hold < 5 or hold > 60
            or any(positive(plan.get(key)) is None for key in ("entry", "stop", "tp1", "tp2", "invalidation"))):
        raise DataError("A complete directional trade plan with at least five minutes remaining is required.")
    direction = plan["direction"]
    if not 0 < direction * (plan["tp1"] - plan["entry"]) < direction * (plan["tp2"] - plan["entry"]):
        raise DataError("Trade Plan targets are out of sequence.")
    if direction * (plan["entry"] - plan["stop"]) <= 0:
        raise DataError("Trade Plan stop is on the wrong side of entry.")
    fit = fitting_sample(candles, asof)
    bounds = rth_bounds(asof.astimezone(ET).date())
    if bounds is None:
        raise DataError("A regular trading session is required.")
    start, close = bounds
    first = asof.replace(second=0, microsecond=0)
    if first < asof:
        first += MINUTE
    if not start <= first or first + timedelta(minutes=hold) > close:
        raise DataError("The simulation horizon exceeds the regular session.")
    clock = max(0, int((first - start).total_seconds() / 60))
    flow = plan.get("flow") or {}
    momentum = min(1., abs(flow.get("momentum", 0)) / (3 * max(1, flow.get("floor", 1))))
    gamma = plan.get("gex")
    gamma_tilt = 1. if gamma is not None and gamma < 0 else -1. if gamma is not None and gamma > 0 else 0.
    out = {}
    for label, model, jump in (("Conditioned block", "block", 0.), ("Gaussian baseline", "gaussian", 0.),
                               ("Jump stress", "block", .1)):
        if jump and not include_stress:
            continue
        paths = simulate_paths(fit, spot, hold, clock, n=n, seed=seed, model=model, regime=plan.get("regime"),
                               flow_pressure=momentum, gamma_pressure=gamma_tilt, jump_fraction=jump)
        comparison = {}
        for strategy in strategies:
            result = path_outcomes(paths, plan, strategy)
            if result is not None:
                comparison[strategy] = summarize_paths(paths, result, plan, option=option,
                                                       cost_bps=plan.get("underlying_cost_bps", 1.0))
        out[label] = comparison
    if option:
        from dataclasses import asdict

        option = asdict(option)
    return {"version": MODEL_VERSION, "models": out, "training_minutes": fit["count"], "sessions": fit["sessions"],
            "horizon": hold, "mode": "option" if option else "underlying", "asof": asof.isoformat(),
            "plan": {k: plan.get(k) for k in ("symbol", "expiry", "setup", "direction", "status", "price", "trigger",
                                               "entry", "invalidation", "stop", "tp1", "tp2", "bar", "demo", "underlying_cost_bps")},
            "option_position": option}
