"""Regression coverage for V2.5 decision, execution and observation boundaries."""
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from dealer_hedging import hedge_change, hedge_scenarios, hedge_snapshot, level_crossings
from decision_engine import make_trade_plan, paper_outcome
from decision_flow import apply_model_gate, build_decision, get_hedge, usable_exposure
from desk_analytics import MINUTE, PriceLevel, price_levels
from discord_monitor import WebhookError, deliver, send_test
from iv_decision import iv_read
from market_session import rth_bounds
from monitor_engine import process_scan
from option_payoff import create_position, position_risk, quoted_legs
from research_journal import Journal
from research_snapshot import publish_snapshot, shared_snapshot
from terminal_data import chart_buckets
from terminal_demo import DEMO_AT, DemoStore
from terminal import PAGES_LABEL
from terminal_views import Desk, read_workspace
from tradytics_feed import Feed
from trade_probability import evaluate_plan, fitting_sample, path_outcomes, summarize_paths
from trade_validation import hedge_outcomes, paper_records, performance

def synthetic_inputs(direction=1):
    day = DEMO_AT.date()
    opened, _ = rth_bounds(day)
    candles = []
    for i in range(80):
        price = 100 + direction * i * .04
        candles.append({"time_utc": (opened + i * MINUTE).isoformat(), "open": price - direction * .04,
                        "close": price, "high": max(price, price - direction * .04) + .025,
                        "low": min(price, price - direction * .04) - .025,
                        "vwap": price, "volume": 2500 if i >= 75 else 1000, "closed": True})
    buckets = chart_buckets(candles, 5)
    flow = [{"time": bar["time_utc"], "net_premium_usd": direction * (100000 if i < 13 else 500000)}
            for i, bar in enumerate(buckets)]
    now = opened + 80 * MINUTE
    return dict(candles=candles, buckets=buckets, flow_rows=flow, levels=price_levels(buckets, day),
                now=now, session=day, expiry=day.isoformat(), dte=0, spot=candles[-1]["close"],
                regime={"label": "Expanding", "atr": .5},
                iv={"valuation": "Unavailable", "atm": None, "ratio": None, "remaining_up": None, "remaining_down": None},
                gamma={}, quality_reasons=[], quote_at=now.isoformat(), minutes_left=310, source="Tradytics")


@pytest.mark.parametrize("direction,state", [(1, "ENTER LONG"), (-1, "ENTER SHORT")])
def test_entry_geometry_direction_and_safety_gates(direction, state):
    args = synthetic_inputs(direction)
    plan = make_trade_plan(**args)
    assert plan["status"] == state
    assert direction * (plan["entry"] - plan["invalidation"]) > 0
    assert direction * (plan["invalidation"] - plan["stop"]) > 0
    assert direction * (plan["tp2"] - plan["tp1"]) > 0
    assert make_trade_plan(**{**args, "quality_reasons": ["Quote is stale."]})["status"] == "WAIT"
    assert make_trade_plan(**{**args, "flow_rows": [{**row, "net_premium_usd": -row["net_premium_usd"]}
                                                    for row in args["flow_rows"]]})["status"] == "AVOID"
    wall = PriceLevel("Call wall", plan["entry"] + direction * .08, "dealer")
    assert make_trade_plan(**{**args, "levels": args["levels"] + [wall]})["status"] == "AVOID"
    lower = {"p_tp1": .3, "ev_ci": (-.7, -.2), "baseline_ev_ci": (-.8, -.15)}
    assert apply_model_gate(plan, lower)["status"] == "AVOID"
    neutral = {"p_tp1": .55, "baseline_p_tp1": .5, "stress_p_tp1": .46,
               "ev_ci": (-.08, .25), "baseline_ev_ci": (-.13, .19)}
    assert apply_model_gate({**plan, "premium_review_required": True}, neutral)["status"] == "GET READY"


def test_hedge_units_and_missing_component():
    now = DEMO_AT
    stamp = (now - MINUTE).isoformat()
    data = {name: {"total": n, "time": stamp, "spot": 100, "unit": unit}
            for name, n, unit in (("gamma", 10000, "usd_per_1pct_move"), ("vanna", 2000, "usd_per_vol_point"),
                                  ("charm", 1440, "shares_per_day"), ("delta", 1500, "shares"))}
    snapshot = hedge_snapshot(data, now)
    assert hedge_change(snapshot, 2, 1, 5)["total"] == -225
    assert hedge_change(snapshot, 2, 1, 5)["notional"] == -22500
    assert hedge_scenarios(snapshot, 110, 1, 30) == []
    assert len(hedge_scenarios(snapshot, 100, 1, 12)) == 6
    del data["vanna"]
    snapshot = hedge_snapshot(data, now)
    assert not snapshot["complete"] and hedge_change(snapshot, 2, 1, 5)["total"] is None
    assert level_crossings(99, 101, {"flip": 100})[0]["direction"] == "above"


def test_iv_exact_expiry_and_anchored_band():
    d = DemoStore("SPY")
    data = d.get("volatility").obj
    regime = {"label": "Compressing"}
    res = iv_read(data, d.expiries[0], DEMO_AT, d.spot, regime, usable=True)
    assert res["atm"] == data["term"][0]["atm_iv"] and res["band"] is None
    res = iv_read(data, d.expiries[1], DEMO_AT, d.spot + 1, regime, usable=True)
    assert res["remaining_up"] == pytest.approx(data["expected_move"]["move_usd"] - 1)
    assert res["rank30"] == data["iv_rank"]
    assert iv_read(data, "2026-11-19", DEMO_AT, d.spot, regime, usable=True)["atm"] is None


def test_intraminute_stop_order_and_pending_no_fill():
    plan = {"entry": 100, "price": 100, "direction": 1, "stop": 99, "tp1": 101.5, "tp2": 102.5,
            "invalidation": 99.5, "range": (98, 101), "ema_level": 100}
    paths = {"close": np.array([[100., 102., 101.2, 101.9, 102.6], [100., 100.1, 100., 100.3, 100.2]]),
             "low": np.array([[98.8, 100.9, 101.15, 101.7], [99.9, 99.9, 99.9, 100.1]]),
             "high": np.array([[102.6, 102.15, 102., 102.7], [100.2, 100.2, 100.4, 100.4]])}
    result = path_outcomes(paths, plan, "Immediate")
    assert list(result["state"]) == ["STOP", "TIME LIMIT"]
    assert not result["tp1"][0]
    assert summarize_paths(paths, result, plan)["p_stop_first"] == .5
    result = path_outcomes(paths, plan, "Breakout and retest")
    assert list(result["fill"]) == [-1, -1]
    assert summarize_paths(paths, result, plan)["p_no_fill"] == 1
    assert path_outcomes(paths, plan, "VWAP retest") is None


def chain_row(symbol, root, strike, right, now, *, bid=2., ask=2.1, **kwargs):
    expiry = now.date().isoformat()
    return {"symbol": symbol, "contract": f"{root}{expiry[2:].replace('-', '')}{right[0].upper()}{int(strike*1000):08d}",
            "expiry": expiry, "type": right, "strike": strike, "bid": bid, "ask": ask, "iv": .20,
            "updated_at": (now - timedelta(seconds=15)).isoformat(), **kwargs}


def test_option_series_and_quote_validation():
    rows = [chain_row("SPX", "SPXW", 7600, "call", DEMO_AT), chain_row("SPX", "SPX", 7600, "call", DEMO_AT),
            chain_row("SPX", "SPXW", 7610, "call", DEMO_AT, multiplier=10)]
    assert len(quoted_legs(rows, "SPX", DEMO_AT.date().isoformat(), DEMO_AT)) == 1
    rows = [chain_row("SPY", "SPY", 760, "call", DEMO_AT),
            chain_row("SPY", "SPY", 762, "call", DEMO_AT, bid=.8, ask=.85)]
    first, second = quoted_legs(rows, "SPY", DEMO_AT.date().isoformat(), DEMO_AT)
    position = create_position("Debit vertical", 1, first, second, DEMO_AT, 15, fee=.65, slippage=.02)
    assert position_risk(position)["max_loss"] == pytest.approx(136.6)
    with pytest.raises(Exception, match="strike order"):
        create_position("Debit vertical", 1, second, first, DEMO_AT, 15, fee=.65, slippage=.02)
    assert not quoted_legs(rows, "SPY", DEMO_AT.date().isoformat(), DEMO_AT + 3 * MINUTE)


def test_forecasts_only_fit_past_and_charge_quote_costs(candles):
    day = "2026-11-03"
    opened, _ = rth_bounds(day)
    origin = opened + 100 * MINUTE
    historical = [row for row in candles if datetime.fromisoformat(row["time_utc"]) < origin]
    n = fitting_sample(historical, origin)["count"]
    assert fitting_sample(candles, origin)["count"] == n
    spot = historical[-1]["close"]
    plan = {"price": spot, "entry": spot, "stop": spot - .6, "invalidation": spot - .5, "tp1": spot + 1.2,
            "tp2": spot + 2.0, "hold": 15, "direction": 1, "range": (spot - 1, spot + 1),
            "ema_level": spot - .2, "vwap": spot - .3}
    strike = round(spot)
    leg = quoted_legs([chain_row("SPY", "SPY", strike, "call", origin)], "SPY", day, origin)[0]
    cheap = create_position("Long option", 1, leg, None, origin, 15, fee=0, slippage=0)
    cost = create_position("Long option", 1, leg, None, origin, 15, fee=1, slippage=.05)
    result = evaluate_plan(candles, plan, origin, n=300, option=cheap)
    more = evaluate_plan(candles, plan, origin, n=300, option=cost)
    immediate = result["models"]["Conditioned block"]["Immediate"]
    after = more["models"]["Conditioned block"]["Immediate"]
    assert after["ev_pnl"] < immediate["ev_pnl"]
    assert after["p_tp1"] == immediate["p_tp1"]
    assert immediate["option_detail"]["max_loss"] == pytest.approx(210.)
    assert "Gaussian baseline" in result["models"] and "Jump stress" in result["models"]


def test_paper_stop_and_hedge_horizon_are_measured_after_origin():
    args = synthetic_inputs()
    plan = make_trade_plan(**args)
    start = args["now"]
    bars = [{"time_utc": (start - MINUTE).isoformat(), "open": plan["entry"], "high": 200, "low": 1, "close": plan["entry"]},
            {"time_utc": start.isoformat(), "open": plan["entry"], "high": plan["tp2"] + 1,
             "low": plan["stop"] - 1, "close": plan["entry"]}]
    assert paper_outcome(plan, bars, start) is None
    assert paper_outcome(plan, bars, start + MINUTE)["state"] == "STOP"
    snapshot = {"spot": 100., "gamma_shares_per_point": 50., "vanna_shares_per_iv_point": 4., "charm_shares_per_day": 300.}
    event = {"origin": start.isoformat(), "payload": {"spot": 100., "snapshot": snapshot,
             "scenarios": hedge_scenarios(snapshot, 100, 1, 5)}}
    assert hedge_outcomes(event, [], start + 4 * MINUTE) is None
    assert hedge_outcomes(event, [{"time_utc": (start + 4 * MINUTE).isoformat(), "close": 101.}],
                          start + 4 * MINUTE) is None
    expected = hedge_outcomes(event, [{"time_utc": (start + 4 * MINUTE).isoformat(), "close": 101.}], start + 5 * MINUTE)
    assert len(expected["outcomes"]) == 3 and expected["outcomes"][0]["observed_price"] == 101.


def test_journal_mirror_and_webhook_delivery_dedup(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "runtime.sqlite3", dsn="")
    assert journal.insert("a", "SPY", "decision", "Tradytics:0", DEMO_AT, {"status": "WAIT"})
    assert not journal.insert("a", "SPY", "decision", "Tradytics:0", DEMO_AT, {})
    files = {}
    shared = Mock()
    shared.read.side_effect = lambda path, default: (files.get(path, default), None)
    shared.write.side_effect = lambda path, value, *args: files.update({path: value})
    assert publish_snapshot(journal, shared, ["SPY"], DEMO_AT)
    assert not publish_snapshot(journal, shared, ["SPY"], DEMO_AT)
    assert shared_snapshot(shared)[0]["SPY"]["decision"][0]["id"] == "a"
    shared.configured = False
    post = Mock(return_value=Mock(status_code=204))
    monkeypatch.setattr("discord_monitor.requests.post", post)
    message = {"symbol": "SPY", "status": "WAIT", "bias": "Neutral", "triggered_at_utc": DEMO_AT.isoformat()}
    for _ in range(2):
        deliver(journal, shared, "https://discord.com/api/webhooks/123/test", "alert-a", message)
    assert post.call_count == 1
    assert post.call_args.kwargs["allow_redirects"] is False
    post.return_value.status_code = 403
    with pytest.raises(WebhookError) as error:
        send_test("https://discord.com/api/webhooks/123/private-token")
    assert "private-token" not in str(error.value)


def test_validation_requires_mature_out_of_sample_records():
    assert not performance(paper_records([]))["qualified"]


@pytest.mark.parametrize("symbol", ["SPY", "SPX"])
def test_provider_shaped_inputs_use_live_dashboard_and_worker_assembly(symbol):
    demo = DemoStore(symbol)
    store = SimpleNamespace(get=lambda endpoint, paused, **params: demo.get(endpoint, **params))
    ctx = Desk(symbol, DEMO_AT, store, False, False, 0, True)
    read = read_workspace(ctx)
    plan, iv = build_decision(ctx, read)
    assert plan["symbol"] == symbol and plan["expiry"] == str(demo.day)
    assert plan["price_context_ready"] and not plan["data_reasons"]
    assert iv["atm"] and get_hedge(ctx, read["gamma_feed"])[0]["complete"]
    assert (plan["vwap"] is None) == (symbol == "SPX")
    chain = ctx.get("chain", symbol=symbol, expiry=plan["expiry"])
    assert quoted_legs(chain.rows, symbol, plan["expiry"], DEMO_AT)
    gamma = read["gamma_feed"]
    wrong_expiry = replace(gamma, snapshot=replace(gamma.snapshot, meta={"expiry": demo.expiries[1]}))
    assert not usable_exposure(ctx, "gamma", wrong_expiry)
    timeline = ctx.timeline()
    wrong_symbol = replace(timeline, snapshot=replace(timeline.snapshot, meta={"symbol": "OTHER"}))
    ctx.store = SimpleNamespace(get=lambda endpoint, paused, **params: wrong_symbol if endpoint == "timeline" else
                                demo.get(endpoint, **params))
    assert ctx.timeline().error.status == "CHECK SYMBOL"


def test_worker_transitions_settlement_and_duplicate_suppression(tmp_path, monkeypatch):
    args = synthetic_inputs()
    now = args["now"]
    ctx = SimpleNamespace(now=now, ticker="SPY", session=args["session"])
    journal = Journal(tmp_path / "worker.sqlite3", dsn="")

    def assembled(context, _read):
        plan = make_trade_plan(**{**args, "now": context.now})
        plan.update(symbol=context.ticker, data_reasons=[], price_context_ready=True, dealer_note="Gamma snapshot unavailable")
        return plan, {}

    monkeypatch.setattr("monitor_engine.build_decision", assembled)
    monkeypatch.setattr("monitor_engine.get_hedge", lambda *_: (None, {}))
    monkeypatch.setattr("monitor_engine.model_summary", lambda *_: {
        "version": "test-model", "p_tp1": .55, "baseline_p_tp1": .5, "stress_p_tp1": .46,
        "ev_ci": (-.08, .25), "baseline_ev_ci": (-.13, .19),
    })
    read = {**args, "bars": Feed(), "gamma_feed": Feed()}
    first, plan = process_scan(journal, ctx, read)
    assert [event["status"] for _, event in first] == ["ENTER LONG"]
    ctx.now += timedelta(seconds=30)
    assert not process_scan(journal, ctx, read)[0]
    stop_bar = {"time_utc": now.isoformat(), "open": plan["entry"], "close": plan["stop"],
                "high": plan["entry"], "low": plan["stop"] - .05, "closed": True}
    ctx.now = now + MINUTE
    delayed = Feed(snapshot=SimpleNamespace(meta={"delay_minutes": 15}))
    assert not process_scan(journal, ctx, {**read, "bars": delayed, "candles": [*args["candles"], stop_bar]})[0]
    assert journal.records("SPY", "candidate", pending=True)
    closed = {**read, "candles": [*args["candles"], stop_bar]}
    second, _ = process_scan(journal, ctx, closed)
    assert [event["status"] for _, event in second] == ["EXIT"]
    assert second[0][1]["outcome"]["state"] == "STOP"
    assert journal.records("SPY", "candidate", settled=True)[0]["outcome"]["r"] < 0
    assert not process_scan(journal, ctx, closed)[0]


def test_all_workspace_pages_and_v25_probability_run(monkeypatch):
    monkeypatch.setattr("dotenv.dotenv_values", lambda *args, **kwargs: {})
    for env in ("TRADYTICS_API_KEY", "WEBULL_APP_KEY", "WEBULL_APP_SECRET", "DEFAULT_TICKER", "DISCORD_WEBHOOK_URL",
                "MONITOR_JOURNAL_DSN", "MONITOR_GITHUB_TOKEN"):
        monkeypatch.delenv(env, raising=False)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "terminal.py"), default_timeout=30).run()
    assert not app.exception
    assert any("ENTRY COMMAND CENTER" in m.value for m in app.markdown)
    assert app.button(key="discord_test_button").disabled
    for value in PAGES_LABEL:
        app.radio(key="desk_page").set_value(value).run()
        assert not app.exception, value
    app.radio(key="desk_page").set_value("Forecast").run()
    app.selectbox(key="desk_expiry_mode").set_value("0 DTE").run()
    app.button(key="qd25_run_plan_mc").click().run()
    assert not app.exception
    assert any("Highest modeled EV" in m.value for m in app.markdown)
    app.get("segmented_control")[1].set_value("Quoted option structure").run()
    app.button(key="qd25_run_plan_mc").click().run()
    assert not app.exception
    assert app.session_state["qd25_result"][2]["mode"] == "option"
    assert app.session_state["qd25_result"][2]["models"]["Conditioned block"]["Immediate"]["option_detail"]["max_loss"] > 0
    app.button(key="watch_SPX").click().run()
    app.radio(key="desk_page").set_value("Positioning").run()
    assert not app.exception
    assert app.get("plotly_chart")
