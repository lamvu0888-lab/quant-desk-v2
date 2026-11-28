"""Validate the exact signal-time series and revalue an open paper position at executable quotes."""
from datetime import timedelta
from math import floor
import sqlite3

import psycopg

from terminal_data import numeric, positive, time_or_none
from v3_options import YEAR, calibrated_iv, contract_spec, greeks, value
from v3_provenance import live_record


def approved_series(trade, now):
    rec = trade["plan"].get("contract_recommendation") or {}
    row = rec.get("selected") or {}
    plan = trade["plan"]
    if rec.get("simulated") is not False or rec.get("status") != "qualified" or not row:
        return None, "No qualified live-data contract recommendation for this setup"
    if rec.get("daily_capacity_verified") is False:
        return None, "Only a scenario was available when the paper ledger size was checked"
    origin, until = time_or_none(rec.get("origin")), time_or_none(rec.get("expires_at"))
    quoted, triggered = time_or_none(row.get("quote_at")), time_or_none(trade.get("origin"))
    if (not all((origin, until, quoted, triggered)) or not quoted <= origin <= now < until
            or not triggered <= now or until > quoted + timedelta(seconds=30)):
        return None, "Ranked contract or originating market quote has expired"
    if any(numeric(rec.get(key)) != numeric(plan.get(field)) for key, field in
           (("target", "tp1"), ("stop", "stop"), ("hold", "hold"))):
        return None, "The approved contract scenario differs from the current Trade Plan"
    side = "call" if plan["direction"] == 1 else "put"
    if (rec.get("symbol") != trade["symbol"] or rec.get("direction") != plan["direction"]
            or row.get("symbol") != trade["symbol"] or row.get("type") != side
            or not positive(row.get("ask")) or not positive(row.get("bid")) or row["bid"] > row["ask"]
            or row.get("quantity") not in (1, 2) or isinstance(row["quantity"], bool)):
        return None, "Ranked contract identity, quote or quantity is invalid"
    try:
        spec = contract_spec(row, trade["symbol"])
    except (TypeError, ValueError):
        return None, "Ranked OCC identity or contract specifications are inconsistent"
    if min(spec.last_trade, spec.expires) <= now:
        return None, "Ranked series has reached its last tradable time"
    return spec, None


def new_fill_quantity(row, quote, remaining_risk, budget, fee, premium_stop):
    ask = quote["ask"]
    if not ask or ask > row["ask"] or quote["bid"] <= 0:
        return 0
    amount = row["quantity"]
    per_stop = max(ask * 100 * premium_stop, (ask - (positive(row.get("stop_premium")) or 0)) * 100) + fee * 2
    qty = min(amount, floor(budget / (ask * 100 + fee)), floor(max(0., remaining_risk) / per_stop))
    visible = quote.get("ask_size")
    return min(qty, floor(visible) if visible is not None else 1)


def open_risk(trade, premium_stop, fee):
    """Remaining distance from the current mark to the protected stop, with entry and exit fees."""
    if trade.get("state") != "OPEN":
        return 0.
    rec = trade["plan"].get("contract_recommendation") or {}
    ranked = rec.get("selected") or {}
    entry = trade["entry_ask"]
    stop = min(entry * (1 - premium_stop), positive(ranked.get("stop_premium")) or entry)
    mark = positive(trade.get("mark_bid")) or entry
    return trade["remaining"] * (max(0., mark - stop) * 100 + 2 * fee)


def movement_context(ctx, opened, original):
    if not getattr(ctx, "journal", None):
        return None
    if not original or not opened:
        return None
    try:
        rows = ctx.journal.records(ctx.ticker, "v3_scan", scope=str(ctx.session), limit=8)
    except (sqlite3.Error, psycopg.Error, OSError):
        # This optional context must not interrupt observed-bid risk exits.
        return None
    for record in rows:
        if not live_record(record):
            continue
        sig = record["payload"]["signal"]
        at = time_or_none(sig.get("at"))
        seen = time_or_none(record.get("origin"))
        if (at and seen and opened <= at <= seen <= ctx.now and ctx.now - at <= timedelta(seconds=90)
                and sig.get("symbol") == ctx.ticker and sig.get("source") == ctx.price_provider
                and all(original.get(key) and sig.get(key) == original[key] for key in ("mode", "version"))):
            return sig
    return None


def reprice(trade, ctx, quote, fee):
    """Use only the pinned series. Model estimates cannot generate a paper fill."""
    rec = trade["plan"].get("contract_recommendation") or {}
    selected = rec.get("selected") or {}
    underlying = ctx.chart_quote
    ask = positive(quote.get("ask"))
    if not (ask and selected and underlying and trade["contract"] == selected["contract"]):
        return None
    at, bid = quote["at"], quote["bid"]
    if (ask < bid or not 0 <= (ctx.now - at).total_seconds() <= 30
            or not 0 <= (ctx.now - underlying["at"]).total_seconds() <= 30
            or abs((at - underlying["at"]).total_seconds()) > 10):
        return None
    spec = contract_spec(selected, ctx.ticker)
    spot = underlying["price"]
    end = min(spec.expires,spec.last_trade,time_or_none(trade["deadline"]))
    if not ctx.now < end:
        return None
    plan = trade["plan"]
    rate = numeric(selected.get("rate"))
    dividend = numeric(selected.get("dividend"))
    if rate is None or dividend is None:
        from settings import Settings
        cfg = Settings()
        rate = cfg.risk_free_rate if rate is None else rate
        dividend = cfg.dividend_yield if dividend is None else dividend
    iv = calibrated_iv(spec, spot, ctx.now, (ask + bid) / 2, rate, dividend)
    if iv is None:
        return None
    remaining = max(0., (spec.expires - end).total_seconds()) / YEAR
    years = (spec.expires - ctx.now).total_seconds() / YEAR
    concession = (ask - bid) / 2
    target = max(0., float(value(spec, plan["tp1"], remaining, iv, rate, dividend)) - concession)
    stop = max(0., float(value(spec, plan["stop"], remaining, iv, rate, dividend)) - concession)
    theta = float(greeks(spec, spot, years, iv, rate, dividend)["theta"])
    upside = (target - bid) * 100 * trade["remaining"]
    downside = max(0., bid - stop) * 100 * trade["remaining"]
    sig = movement_context(ctx, time_or_none(trade["opened"]), plan.get("movement_v3")) or {}
    start_iv = positive(selected.get("iv"))
    return dict(at=at.isoformat(), underlying_at=underlying["at"].isoformat(), bid=bid, basis="Observed bid and midpoint-fitted IV",
                target_bid=target, stop_bid=stop, theta_per_day=theta, iv=iv,
                iv_change=iv - start_iv if start_iv else None, remaining_upside=upside,
                reward_risk=upside / downside if downside > 0 else None,
                net_at_target=(target - trade["entry_ask"]) * 100 * trade["remaining"] - 2 * fee * trade["remaining"],
                continuation=(sig.get("scores") or {}).get("continuation"),
                exhaustion=(sig.get("scores") or {}).get("exhaustion"), movement=sig.get("state"),
                movement_at=sig.get("at"), forecast_at=end.isoformat())
