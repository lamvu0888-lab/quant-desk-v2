"""Shared assembly of live decision inputs and model risk checks."""
from __future__ import annotations

from datetime import datetime

from data_utils import DataError, field, utc
from dealer_hedging import UNITS, hedge_snapshot, level_crossings
from decision_engine import ENTRY_STATES, make_trade_plan
from desk_analytics import FIVE_MINUTES, MINUTE, candles_on
from iv_decision import iv_read
from market_session import ET, session_state
from movement_detector import cvd_context
from option_quotes import atm_iv_sample, option_review, quote_age
from terminal_data import (INDICES, exposure_obj, feed_has_delay, numeric, positive, same_ticker,
                           source_stamp, time_or_none)
from trade_probability import evaluate_plan
from tradingview_levels import dark_levels, dealer_levels
from tradytics_feed import Feed


def usable_exposure(ctx, greek, feed):
    obj = exposure_obj(feed, ctx.ticker, greek)
    stamp = source_stamp("exposure", feed, ctx.ticker)
    meta = feed.snapshot.meta if feed.snapshot else {}
    scope_matches = (obj.get("expiry") in (None, ctx.expiry) and meta.get("expiry") in (None, ctx.expiry)
                     and meta.get("symbol") in (None, ctx.ticker))
    if (not obj or not scope_matches or obj.get("unit") != UNITS[greek] or feed.error or feed_has_delay(feed) or ctx.paused or
            not isinstance(stamp, datetime) or stamp.astimezone(ET).date() != ctx.session or
            not 0 <= (ctx.now - stamp).total_seconds() <= 420):
        return {}
    return obj


def surface_is_usable(ctx, feed):
    obj = feed.obj
    stamp = source_stamp("volatility", feed, ctx.ticker)
    return bool(obj and obj.get("symbol") in (None, ctx.ticker) and stamp == ctx.session
                and not feed.error and not ctx.paused and not feed_has_delay(feed))


def get_hedge(ctx, gamma_feed=None):
    feeds = {"gamma": gamma_feed if gamma_feed is not None else ctx.exposure("gamma")}
    feeds.update({name: ctx.exposure(name) for name in ("vanna", "charm", "delta")})
    objects = {name: usable_exposure(ctx, name, feed) for name, feed in feeds.items()}
    return hedge_snapshot(objects, ctx.now, preview=ctx.preview), feeds


def price_quote_issues(ctx, quote):
    if ctx.paused:
        return ["Price polling is paused."]
    stamp = time_or_none(field(quote, "price_at", "time"))
    if ctx.price_provider == "Webull":
        if ctx.webull_quality.get("quote_ready"):
            return []
        return ["Waiting for a fresh Webull price."]
    issues = []
    quote_feed = ctx.quote_feed()
    if (positive(quote.get("price")) is None or stamp is None or stamp.astimezone(ET).date() != ctx.session
            or not 0 <= (ctx.now - stamp).total_seconds() <= 120 or quote.get("stale") is True
            or quote.get("session") not in (None, "regular")):
        issues.append("Waiting for a current Tradytics regular-session price.")
    if quote_feed.error or feed_has_delay(quote_feed) or (numeric(quote.get("delay_minutes")) or 0) > 0:
        issues.append("Tradytics price feed has a reported delay or error.")
    if not quote_feed.snapshot:
        issues.append("Waiting for a Tradytics quote snapshot.")
    return issues


def read_iv(ctx, vol, regime, spot, expiry, webull_chain=None):
    if webull_chain is None:
        webull_chain = ctx.option_chain(expiry, provider="Webull")[0] if expiry and ctx.has_webull_options else Feed()
    sample = atm_iv_sample(webull_chain, ctx.ticker, expiry, spot, ctx.now,
                           max_age=ctx.option_max_age, paused=ctx.paused)
    report = iv_read(vol.obj, expiry, ctx.now, spot, regime, usable=surface_is_usable(ctx, vol), atm_sample=sample)
    if report["atm"] is not None and not sample:
        # Tradytics receipt time can advance with an unchanged upstream surface.
        stamp = time_or_none(field(vol.obj, "time", "updated_at"))
        if ctx.preview and vol.snapshot:
            stamp = min(vol.snapshot.received_at, ctx.now)
            report["atm_source"] = "Preview"
        report["atm_at"] = stamp.isoformat() if stamp else None
    else:
        report["atm_at"] = sample["as_of"] if sample else None
    return report


def build_decision(ctx, read, *, warm_options=True):
    quote = ctx.quote()
    spot = positive(quote.get("price"))
    stamp = time_or_none(field(quote, "price_at", "time"))
    quote_issues = price_quote_issues(ctx, quote)
    quality = list(dict.fromkeys([*read["reasons"], *quote_issues]))
    gamma = usable_exposure(ctx, "gamma", read["gamma_feed"])
    use_webull_chain = bool(ctx.expiry and ctx.has_webull_options)
    webull_chain = ctx.option_chain(ctx.expiry, provider="Webull")[0] if use_webull_chain and warm_options else Feed()
    iv = read_iv(ctx, read["vol"], read["regime"], spot, ctx.expiry, webull_chain=webull_chain)
    gamma_levels = dealer_levels(gamma, spot, 0)
    session = session_state(ctx.now)
    def assemble(levels):
        return make_trade_plan(candles=read["candles"], buckets=read["buckets"], flow_rows=read["flow"].rows,
                               levels=levels, now=ctx.now, session=ctx.session, expiry=ctx.expiry, dte=ctx.dte,
                               spot=spot, regime=read["regime"], iv=iv, gamma=gamma, quality_reasons=quality,
                               quote_at=stamp.isoformat() if stamp else None, minutes_left=session["minutes_left"],
                               source=ctx.price_provider, preview=ctx.preview,
                               flow_usable=read.get("flow_state", "Aligned") == "Aligned")

    levels = read["levels"] + gamma_levels
    plan = assemble(levels)
    if plan["status"] in (*ENTRY_STATES, "GET READY") and ctx.ticker not in INDICES:
        dark = ctx.get("dark_levels", symbol=ctx.ticker, window=5, limit=30)
        dark_day = source_stamp("dark_levels", dark, ctx.ticker)
        if dark.snapshot and not dark.error and not feed_has_delay(dark) and dark_day == ctx.session:
            levels += dark_levels(same_ticker(dark.rows, ctx.ticker), dark.snapshot.received_at.isoformat(), 3)
            plan = assemble(levels)
    if use_webull_chain and not warm_options and plan["status"] in (*ENTRY_STATES, "GET READY"):
        webull_chain, _ = ctx.option_chain(ctx.expiry, provider="Webull")
        iv = read_iv(ctx, read["vol"], read["regime"], spot, ctx.expiry, webull_chain=webull_chain)
        if iv["atm_sample"]:
            plan = assemble(levels)
    plan["symbol"] = ctx.ticker
    plan["underlying_cost_bps"] = 1.0
    plan["data_reasons"] = quality
    bar_at = time_or_none(plan["bar"])
    gamma_at = source_stamp("exposure", read["gamma_feed"], ctx.ticker) if gamma else None
    minute_close = read.get("minute_close")
    flow_stamp = read.get("flow_stamp")
    plan["flow_at"] = flow_stamp.isoformat() if flow_stamp else plan["flow"].get("stamp")
    plan["gamma_at"] = gamma_at.isoformat() if gamma_at else None
    plan["feed_timing"] = {"quote_age": quote_age(stamp, ctx.now), "quote_ready": not quote_issues,
                           "minute_at": minute_close.isoformat() if minute_close else None,
                           "minute_age": quote_age(minute_close, ctx.now),
                           "flow_at": flow_stamp.isoformat() if flow_stamp else None,
                           "flow_age": quote_age(flow_stamp, ctx.now), "flow_state": read.get("flow_state", "Waiting"),
                           "dealer_age": quote_age(gamma_at, ctx.now)}
    plan["iv_source"], plan["iv_at"] = iv["atm_source"], iv["atm_at"]
    plan["price_watch"] = read.get("price_watch")
    if use_webull_chain and (warm_options or plan["status"] in (*ENTRY_STATES, "GET READY")):
        review = option_review(webull_chain, "Webull", ctx.ticker, ctx.expiry, spot, plan["direction"], ctx.now,
                               max_age=ctx.option_max_age, paused=ctx.paused)
        plan["option_quote"] = review
        if plan["status"] in ENTRY_STATES and not review["ready"]:
            plan["status"] = "GET READY"
            plan["missing"].append(f"Webull option check: {review['status']}.")
    plan["cvd"] = cvd_context(read["candles"], ctx.session, time_or_none(plan["entry_bar"]),
                              f"{ctx.ticker} estimated share CVD")
    if ctx.ticker in INDICES:
        plan["cvd"] = {"label": "Native share CVD unavailable for this index", "delta": None, "close": None}
    plan["price_context_ready"] = bool(not quote_issues and read.get("bars_ready", True) and not ctx.paused and not read["bars"].error and
                                       not feed_has_delay(read["bars"]) and bar_at and
                                       0 <= (ctx.now - bar_at).total_seconds() <= (FIVE_MINUTES + 2 * MINUTE).total_seconds())
    plan["dealer_note"] = ("Negative GEX can amplify moves" if plan["gex"] is not None and plan["gex"] < 0 else
                           "Positive GEX can dampen moves" if plan["gex"] is not None and plan["gex"] > 0 else
                           "Model flat" if plan["gex"] == 0 else "Gamma snapshot unavailable")
    plan["iv_guidance"] = iv["guidance"]
    plan["reference_move"] = iv["reference_move"]
    plan["premium_review_required"] = bool(ctx.expiry and (iv["ratio"] or 0) >= 1.5 and
                                           max(iv["rank30"] or 0, iv["percentile30"] or 0) >= .8)
    levels = gamma.get("levels") if isinstance(gamma.get("levels"), dict) else {}
    plan["gamma_levels"] = {key: positive(levels.get(key)) for key in ("flip", "call_wall", "put_wall")}
    if spot and gamma_levels:
        wall = min((level for level in gamma_levels if level.label.endswith("wall")),
                   default=None, key=lambda level: abs(level.price - spot))
        if wall:
            plan["nearest_wall"] = {"label": wall.label, "price": wall.price, "distance": wall.price - spot}
    quote_feed = ctx.quote_feed() if ctx.price_provider == "Tradytics" else None
    if quote_feed and quote_feed.snapshot:
        delay = numeric(quote.get("delay_minutes"))
        if delay is None:
            delay = numeric(quote_feed.snapshot.meta.get("delay_minutes"))
        plan["delay_status"] = "Provider reports zero delay" if delay == 0 else "Provider did not specify quote delay" if delay is None else "Provider reports delay"
    elif ctx.price_provider == "Webull":
        delay = ctx.webull_quality.get("delay_minutes")
        plan["delay_status"] = ("Webull reports zero delay" if delay == 0 else "Webull reports delay" if delay else
                                "Webull quote freshness is checked using its market timestamp")
    day = candles_on(read["buckets"], ctx.session)
    plan["crossings"] = (level_crossings(day[-2]["close"], day[-1]["close"], plan["gamma_levels"])
                         if len(day) >= 2 and not quality and gamma_at and gamma_at <= utc(day[-1]["time_utc"]) else [])
    return plan, iv


def model_summary(candles, plan, asof, *, n=1000, include_stress=True, strategies=("Immediate",)):
    try:
        result = evaluate_plan(candles, plan, asof, n=n, strategies=strategies, include_stress=include_stress)
    except DataError as exc:
        return {"error": str(exc)}
    base = result["models"]["Gaussian baseline"].get("Immediate") or {}
    main = result["models"]["Conditioned block"].get("Immediate") or {}
    stress = result["models"].get("Jump stress", {}).get("Immediate") or {}
    return {"version": result["version"], "p_tp1": main.get("p_tp1"), "p_tp2": main.get("p_tp2"),
            "p_stop": main.get("p_stop_before_tp2"), "baseline_p_tp1": base.get("p_tp1"),
            "ev_r": main.get("ev_r"), "ev_ci": main.get("ev_ci"), "baseline_ev_ci": base.get("ev_ci"),
            "stress_ev_r": stress.get("ev_r"), "stress_p_tp1": stress.get("p_tp1"), "paths": n,
            "horizon": result["horizon"], "sessions": result["sessions"]}


def apply_model_gate(plan, model, validation=None):
    """The model may veto an entry. Positive exploratory EV cannot authorize one."""
    plan = {**plan, "model": model, "blocks": list(plan["blocks"]), "missing": list(plan["missing"])}
    if validation:
        plan["validation_sample"] = validation.get("n", 0)
        if validation.get("qualified"):
            plan["validation"] = "Prospectively evaluated · TP1"
        if validation.get("expectancy_qualified"):
            plan["expectancy_validation"] = "Prospectively evaluated · underlying paper R"
    if plan["status"] not in ENTRY_STATES:
        return plan
    if model.get("p_tp1") is None:
        plan["status"] = "GET READY"
        plan["missing"].append(model.get("error") or "Comparable price-path simulation.")
    elif model["ev_ci"][1] < 0 and model["baseline_ev_ci"] and model["baseline_ev_ci"][1] < 0:
        plan["status"] = "AVOID"
        plan["blocks"].append("Both price-path models estimate negative expectancy after the modeled cost.")
    elif (model["baseline_p_tp1"] is not None and abs(model["p_tp1"] - model["baseline_p_tp1"]) > .25
          or model["stress_p_tp1"] is not None and abs(model["p_tp1"] - model["stress_p_tp1"]) > .20):
        plan["status"] = "GET READY"
        plan["missing"].append("Probability estimates differ materially between the scenario models.")
    elif validation and validation.get("expectancy_qualified") and validation.get("realized_ev_ci", [0, 0])[1] < 0:
        plan["status"] = "AVOID"
        plan["blocks"].append("Comparable paper outcomes show negative realized expectancy.")
    elif plan.get("premium_review_required"):
        plan["status"] = "GET READY"
        plan["missing"].append("Review the executable option premium. Exact-expiry ATM IV is at least 1.50× trailing RV and the 30-day IV rank or percentile is at least 80%.")
    return plan


def current_minute_close(candles):
    return (utc(candles[-1]["time_utc"]) + MINUTE) if candles else None
