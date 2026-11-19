from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from dealer_hedging import UNITS
from decision_engine import ENTRY_STATES, make_trade_plan
from decision_flow import apply_model_gate, build_decision
from desk_analytics import MINUTE, PriceLevel, price_levels
from discord_monitor import MonitorScan, deliver
from market_session import rth_bounds
from movement_detector import movement_status
from paper_engine import PaperLedger, fast_setup, valid_plan
from research_journal import Journal
from settings import Settings
from signal_explain import discord_text
from range_reversal import RANGE_SETUP
from terminal_data import chart_buckets
from terminal_demo import DEMO_AT
from terminal_views import Desk, read_workspace
from test_hybrid_feeds import option_pair, snapshot, webull_prices
from test_v25 import synthetic_inputs
from tradytics_feed import Feed


def range_inputs(side=1, *, minutes=120, scale=1.0):
    """A two-sided market followed by a rejection at the low, or its price mirror."""
    day = DEMO_AT.date()
    start, _ = rth_bounds(day)
    anchors = {0: 99, 12: 104, 23: 98, 36: 104, 49: 98, 62: 104, 73: 98,
               85: 104, 96: 98, 101: 98.25, 108: 98.15, 113: 98.18,
               114: 98.15, 115: 98.30, 116: 98.60, 117: 98.72, 118: 98.85, 119: 98.90}
    offsets, prices = list(anchors), list(anchors.values())
    vals = [float(100 + side * scale * (p - 100)) for p in np.interp(range(minutes), offsets, prices)]
    candles = []
    for i, close in enumerate(vals):
        opened = vals[max(0, i - 1)]
        high, low = max(opened, close) + .06 * scale, min(opened, close) - .06 * scale
        if i == 114:
            if side > 0:
                low = 100 + scale * (97.90 - 100)
            else:
                high = 100 - scale * (97.90 - 100)
        candles.append(dict(time_utc=(start + i * MINUTE).isoformat(), open=opened, high=high, low=low, close=close,
                            volume=1000, vwap=(high + low + close) / 3, closed=True))
    bars = chart_buckets(candles, 5)
    flow = [dict(time=b["time_utc"], net_premium_usd=side * (300000 if i >= len(bars)-3 else 60000))
            for i, b in enumerate(bars)]
    now = start + minutes * MINUTE
    return dict(candles=candles, buckets=bars, flow_rows=flow, levels=price_levels(bars, day), session=day,
                now=now, expiry=day.isoformat(), dte=0, spot=vals[-1], source="Schwab", quote_at=now.isoformat(),
                regime={"label": "Normal", "atr": 1.05 * scale}, gamma={"total": 100},
                iv={"valuation": "Unavailable", "atm": None, "ratio": None, "remaining_up": None, "remaining_down": None},
                quality_reasons=[], minutes_left=210)


@pytest.mark.parametrize("side, expected", [(1, "ENTER LONG"), (-1, "ENTER SHORT")])
def test_rejection_and_follow_through(side, expected):
    plan = make_trade_plan(**range_inputs(side))
    assert plan.get("setup") == RANGE_SETUP
    assert plan["status"] == expected, (plan["missing"], plan["blocks"], plan["score_details"])
    assert plan["ema"]["5m"]["direction"] != side
    assert plan["flow_confirmation"]["confirmed"] and plan["score"] >= 71
    assert plan["rr"] >= 1.35
    assert side * (plan["entry"] - plan["trigger"]) > 0
    assert side * (plan["invalidation"] - plan["stop"]) > 0
    box = plan["range_reversal"]
    assert plan["tp1"] == pytest.approx(box["midpoint"], abs=.01)
    assert plan["tp2"] == pytest.approx(box["high"] if side > 0 else box["low"], abs=.01)


def reprice(args, changes):
    """Apply price edits and recompute every downstream candle and level."""
    minutes = [dict(row, **changes.get(i, {})) for i, row in enumerate(args["candles"])]
    bars = chart_buckets(minutes, 5)
    return {**args, "candles": minutes, "buckets": bars, "spot": minutes[-1]["close"],
            "levels": price_levels(bars, args["session"])}


def test_wait_for_rejection_and_later_confirmation():
    args = range_inputs(minutes=117)
    p = make_trade_plan(**args)
    assert p["status"] == "GET READY" and not p["range_reversal"]["follow_through"]
    assert p["range_reversal"]["as_of"] < args["candles"][-3]["time_utc"]
    # Deliver additional (even fully marked) future bars before the clock reaches their close.
    future = range_inputs()
    early = make_trade_plan(**{**args, "candles": future["candles"], "buckets": future["buckets"],
                               "flow_rows": future["flow_rows"]})
    assert early["range_reversal"] == p["range_reversal"] and early["status"] == p["status"]
    forming = reprice(future, {119: {"closed": False, "forming": True}})
    assert make_trade_plan(**forming)["status"] not in ENTRY_STATES


def test_width_rotation_and_location_filters():
    assert not make_trade_plan(**range_inputs(scale=.025)).get("range_reversal")
    assert not make_trade_plan(**synthetic_inputs()).get("range_reversal")
    assert not make_trade_plan(**range_inputs(minutes=55)).get("range_reversal")
    args = range_inputs()
    middle = reprice(args, {119: {"open": 98.85, "high": 101.25, "low": 98.80, "close": 101.20}})
    p = make_trade_plan(**middle)
    assert p["status"] == "WAIT" and p["range_reversal"]["direction"] == 0
    assert movement_status({**p, "data_reasons": []}) == "SIDEWAYS"
    held = reprice(args, {i: {"open": 97.2, "high": 97.4, "low": 97.0, "close": 97.2} for i in [118, 119]})
    assert not make_trade_plan(**held).get("range_reversal")
    returned = reprice(args, {i: {"open": 97.2, "low": 97.1, "high": 97.45, "close": 97.3} for i in [117, 118]})
    assert (make_trade_plan(**returned).get("range_reversal") or {}).get("direction", 0) == 0
    for edit in ({"low": 97.30}, {"high": 101.15}):
        p = make_trade_plan(**reprice(args, {118: edit}))
        assert p["status"] not in ENTRY_STATES and not (p.get("range_reversal") or {}).get("follow_through")


def test_shared_gates_and_optional_context():
    args = range_inputs()
    q = lambda **extra: make_trade_plan(**{**args, **extra})
    assert q(quality_reasons=["Stale Schwab quote."])["status"] == "WAIT"
    assert q(spot=100.9)["status"] == "AVOID"
    assert q(flow_rows=args["flow_rows"][:-1])["status"] not in ENTRY_STATES
    assert q(flow_usable=False)["status"] not in ENTRY_STATES
    assert q(flow_rows=[dict(row, net_premium_usd=-row["net_premium_usd"]) for row in args["flow_rows"]])["status"] == "AVOID"
    wall = PriceLevel("Call wall", args["spot"] + .5, "dealer")
    assert q(levels=[wall])["status"] == "AVOID"
    assert q(iv=dict(args["iv"], remaining_up=.5))["status"] == "AVOID"
    assert q(regime=dict(args["regime"], label="Expanding"), gamma={"total": -10})["status"] == "AVOID"
    conservative = q(levels=[PriceLevel("Call wall", 102.0, "dealer")])
    assert conservative["tp1"] < 101.0 and conservative["tp2"] == 102.0
    assert q(dealer_scenario={"total": -1, "horizon": 5, "scenario": "Down / IV up"})["score"] == q()["score"] - 5
    for source, points in (("footprint", 10), ("estimated", 5)):
        p = q(cvd_proxy={"delta": 20, "quality": source})
        assert p["score"] == q()["score"] + points
    assert sum(f["possible"] for f in q()["score_details"]) == 100
    flat_flow = [dict(r, net_premium_usd=50000) for r in args["flow_rows"]]
    borderline = q(gamma={}, flow_rows=flat_flow)
    assert borderline["score"] == 70 and borderline["status"] == "GET READY"
    assert apply_model_gate(q(), {"p_tp1": .2, "ev_ci": (-.7,-.2), "baseline_ev_ci": (-.6,-.1)})["status"] == "AVOID"


@pytest.mark.parametrize("side", [1, -1])
def test_worker_and_paper_journal_route(tmp_path, monkeypatch, side):
    args = range_inputs(side)
    now, symbol, expiry = args["now"], "QQQ", args["expiry"]
    journal = Journal(tmp_path / "reversals.sqlite", dsn="")
    ledger = PaperLedger(journal)
    ledger.start(now - MINUTE)
    option = [Feed()]
    clock = [now]

    def get(name, **params):
        if name == "expirations":
            return snapshot([{"expiry": expiry}], now, symbol=symbol)
        if name == "timeline":
            return snapshot(args["flow_rows"], now, symbol=symbol, interval="5m")
        return Feed()

    store = SimpleNamespace(get=get)
    webull = SimpleNamespace(settings=Settings(), prices=lambda _: {symbol: webull_prices(args["candles"], clock[0])},
                             options=lambda *a, **kw: option[0])
    worker = MonitorScan(store, webull, "Auto", journal)
    monkeypatch.setattr("terminal_views.realized_regime", lambda _: args["regime"])
    monkeypatch.setattr("monitor_engine.model_summary", lambda *_: {
        "p_tp1": .55, "baseline_p_tp1": .51, "stress_p_tp1": .49, "ev_ci": (-.1,.2), "baseline_ev_ci": (-.1,.15)})
    first, _ = worker.run([symbol], now, 30)
    assert not any(p["status"] in ENTRY_STATES for _, p in first)
    clock[0] += MINUTE / 2
    option[0] = option_pair(symbol, expiry, clock[0])
    alerts, _ = worker.run([symbol], clock[0], 30)
    entries = [(i,p) for i,p in alerts if p["status"] in ENTRY_STATES]
    assert len(entries) == 1
    ident, p = entries[0]
    assert p["setup"] == RANGE_SETUP and p["direction"] == side
    assert p["source"] == "Webull" and p["option_quote"]["ready"] and p["range_reversal"]["rejection_at"] in ident
    assert valid_plan(p) and not fast_setup(p)
    assert not [p for _,p in worker.run([symbol], now + MINUTE * .75, 30)[0] if p["status"] in ENTRY_STATES]
    transport = Mock()
    monkeypatch.setattr("discord_monitor.send", transport)
    shared = Mock(configured=True)
    shared.history.return_value = ([], None)
    deliver(journal, shared, None, ident, p)
    deliver(journal, shared, None, ident, p)
    transport.assert_called_once()
    shared.append_event.assert_called_once()
    assert shared.append_event.call_args.args[0]["range_reversal"]["direction"] == side
    message = discord_text(p)
    assert "Range reversal" in message and ("Support bounce" if side > 0 else "Resistance rejection") in message
    assert "Webull price" in message and "Stop" in message and "T2" in message and "ET" in message
    with journal.connection() as db:
        ledger.ingest(db)
        recorded = ledger.rows(db)
    assert len(recorded) == 1 and recorded[0]["plan"]["setup"] == RANGE_SETUP


def schwab_workspace():
    args = range_inputs()
    now, spot, expiry, symbol = args["now"], args["spot"], args["expiry"], "SPX"
    rows = [dict(row, volume=None, vwap=None) for row in args["candles"]]
    warmup = []
    for days_ago in (2, 1):
        start, close = rth_bounds(args["session"] - timedelta(days=days_ago))
        for i in range((close - start) // MINUTE):
            warmup.append(dict(rows[i % 48], time_utc=(start + i * MINUTE).isoformat()))
    quotes = {"quote": snapshot(dict(price=spot, price_at=now.isoformat(), realtime=True), now, symbol=symbol),
              "history": snapshot(dict(candles=warmup + rows), now, symbol=symbol)}
    schwab = SimpleNamespace(get=lambda name, **kw: quotes[name])
    chain = [option_pair(symbol, expiry, now)]

    def get(name, **kw):
        if name == "timeline":
            return snapshot(args["flow_rows"], now, symbol=symbol, interval="5m")
        if name == "chain":
            return chain[0]
        if name == "exposure":
            greek = kw["greek"]
            value = {"gamma": 10000., "vanna": 1., "charm": 0., "delta": 100.}[greek]
            return snapshot(dict(symbol=symbol, greek=greek, expiry=expiry, unit=UNITS[greek], time=now.isoformat(),
                                 spot=spot, total=value), now, expiry=expiry)
        return Feed()

    ctx = Desk(symbol, now, SimpleNamespace(get=get), False, False, 0, True, "Schwab", schwab_store=schwab)
    read = read_workspace(ctx)
    return ctx, read, chain


def test_schwab_underlying_tradytics_options_and_missing_spx_volume():
    ctx, read, chain = schwab_workspace()
    plan, _ = build_decision(ctx, read)
    assert plan["status"] == "ENTER LONG", (plan["missing"], plan["blocks"])
    assert plan["source"] == "Schwab" and plan["option_quote"]["source"] == "Tradytics"
    assert plan["range_reversal"]["dealer_scenario"]["total"] > 0
    assert plan["volume_ratio"] is None and plan["cvd_proxy"] is None
    chain[0] = option_pair("SPX", ctx.expiry, ctx.now - 4 * MINUTE)
    p, _ = build_decision(ctx, read)
    assert p["status"] == "GET READY" and p["option_quote"]["status"] == "Stale quote"
    wrong = (ctx.session + timedelta(days=3)).isoformat()
    chain[0] = option_pair("SPX", wrong, ctx.now)
    p, _ = build_decision(ctx, read)
    assert p["status"] == "GET READY" and not p["option_quote"]["ready"]


def test_rendered_reversal_and_range_chart():
    from streamlit.testing.v1 import AppTest
    ctx, read, _ = schwab_workspace()
    plan, iv = build_decision(ctx, read)
    app = AppTest.from_string(
        "import streamlit as st\nfrom decision_ui import command_panel\nfrom movement_ui import detector_view\n"
        "ctx, read, plan, iv = st.session_state.fixture\n"
        "command_panel(ctx, plan, iv, read['candles'], key='trade')\ndetector_view(ctx, read, plan, iv)")
    app.session_state["fixture"] = (ctx, read, plan, iv)
    app.run()
    assert not app.exception
    assert any("Range reversal" in m.value and "ENTER LONG" in m.value for m in app.markdown)
    assert any("Midpoint" in c.value and "Resistance" in c.value for c in app.caption)
    from movement_charts import chart_for_stage
    fig = chart_for_stage(5, ctx, read, plan, structure_minutes=3)
    assert "Range midpoint" in {a.text for a in fig.layout.annotations}


def test_width_relative_to_noise_and_trend_release():
    from range_reversal import established_range
    args = range_inputs()
    reference = args["buckets"][10:22]
    assert established_range(reference)
    # A large absolute span is insufficient if the average 5m candle spans much of it.
    noisy = [dict(b, high=max(103.6, b["high"]), low=min(98.4, b["low"])) for b in reference]
    assert established_range(noisy) is None
    trend = {i: dict(open=103+(i-108)*.4, close=103.4+(i-108)*.4,
                    low=102.95+(i-108)*.4, high=103.45+(i-108)*.4) for i in range(108, 120)}
    p = make_trade_plan(**reprice(args, trend))
    assert p["movement"]["released"] and "range_reversal" not in p


def test_paper_validation_keeps_each_setup_with_its_own_rules():
    from decision_engine import RULES_VERSION, TREND_RULES_VERSION
    from trade_probability import MODEL_VERSION
    from trade_validation import paper_records

    args = range_inputs()
    def event(name, version):
        plan = {"symbol": "SPX", "setup": name, "rule_version": version, "hold": 15}
        return {"origin": args["now"].isoformat(), "payload": {
            "plan": plan, "model": {"version": MODEL_VERSION, "p_tp1": .5, "baseline_p_tp1": .5}},
            "outcome": {"r": .8, "at": (args["now"] + 15 * MINUTE).isoformat()}}
    trend = event("EMA 9/21 pullback", TREND_RULES_VERSION)
    entry = event(RANGE_SETUP, RULES_VERSION)
    wrong = event(RANGE_SETUP, TREND_RULES_VERSION)
    assert paper_records([trend, entry, wrong], setup=RANGE_SETUP) == [entry]
    assert paper_records([trend, entry, wrong], setup="EMA 9/21 pullback") == [trend]
