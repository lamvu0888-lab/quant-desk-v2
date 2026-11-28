"""Production-boundary regression checks, using recorded-shape fixtures without network access."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from discord_monitor import deliver
from futures_proxy import price_confirmation
from monitor_engine import price_alert, process_scan
from paper_engine import attempt_entry
from research_journal import Journal
from terminal_demo import DEMO_AT, DemoStore
from terminal_views import Desk, read_workspace
from test_v3 import DAY, CLOSE, desk, fake_records
from tradytics_feed import Feed, Snapshot
from v3_daily import daily
from v3_evaluate import archive_rows
from v3_movement import MINUTE, prior_state, record, resolve_state, signal, transition
from v3_research import archive, market_events, matured, training_records


def test_signed_sources_keep_values_and_clocks_separate():
    ctx, read, plan = desk()
    close = read["minute_close"]
    net = [{"time": (close-i*MINUTE).isoformat(), "net_premium_usd": n}
           for i, n in zip((3,2,1), (10,20,35))]
    points = [{"time": (close-i*MINUTE).isoformat(), "delta": n}
              for i, n in zip((2,1,0), (2,4,9))]
    flow = Feed(Snapshot(net, {"interval":"1m", "symbol":"SPY"}, ctx.now))
    s = signal(ctx, read, plan, flow, points, "Observed ETF footprint", {})
    f = s["features"]
    assert (f["net_value"],f["net_change"],f["net_acceleration"]) == (35,15,5)
    assert (f["footprint_value"],f["footprint_change"],f["footprint_acceleration"]) == (9,5,3)
    assert f["footprint_units"] == "shares" and s["freshness"]["cvd"] == close.isoformat()
    assert s["cross_market"] is None
    duplicate = signal(ctx, read, plan, flow, [*points,points[-1]], "Observed ETF footprint", {})
    assert duplicate["features"]["footprint_acceleration"] is None
    assert duplicate["freshness"]["cvd"] is None and duplicate["features"]["net_acceleration"] == 5
    delayed = Feed(Snapshot(net, {"interval":"1m", "symbol":"SPY", "delay_minutes":15}, ctx.now))
    assert signal(ctx, read, plan, delayed, points, "Observed ETF footprint", {})["freshness"]["netflow"] is None


def test_es_must_have_two_exact_prices():
    ctx, read, _ = desk()
    close = read["minute_close"]
    points = [{"time": (close-MINUTE).isoformat(), "close":100}, {"time":close.isoformat(), "close":101}]
    ref = price_confirmation(points, close, "ESZ6")
    assert ref["symbol"] == "ESZ6" and ref["direction"] == 1 and ref["change_pct"] == pytest.approx(1)
    assert price_confirmation(points[:1], close, "ESZ6") is None
    assert price_confirmation([*points, points[-1]], close, "ESZ6") is None
    assert price_confirmation(points, close+MINUTE, "ESZ6") is None


def test_cold_worker_rechecks_options_on_ignition_during_legacy_wait(monkeypatch):
    import decision_flow
    demo = DemoStore("SPY")
    store = SimpleNamespace(get=lambda endpoint, paused, **params: demo.get(endpoint, **params))
    ctx = Desk("SPY", DEMO_AT, store, False, False, 0, True)
    read = read_workspace(ctx)
    original_assembly = decision_flow.make_trade_plan
    original_chain = ctx.option_chain
    calls = []
    def wait(**data):
        return {**original_assembly(**data), "status":"WAIT", "confirmed":False, "direction":0}
    def chain(expiry, **kwargs):
        calls.append(kwargs.get("provider"))
        return original_chain(expiry, **kwargs)
    monkeypatch.setattr(ctx, "option_chain", chain)
    monkeypatch.setattr(decision_flow, "make_trade_plan", wait)
    monkeypatch.setenv("QD_V3_SELECTION", "false")
    orig_signal = decision_flow.movement_signal
    for state in ("BUILDING", "BULLISH IGNITION"):
        monkeypatch.setattr(decision_flow, "movement_signal",
                            lambda *a: {**orig_signal(*a), "state":state, "side":1})
        calls.clear()
        plan, _ = decision_flow.build_decision(ctx, read, warm_options=False)
        assert calls == (["Tradytics"] if state.endswith("IGNITION") else [])
        assert plan["status"] == "WAIT" and plan["movement_v3"]["state"] == state
        assert plan["demo"]


def test_ui_preview_cannot_write_paper_or_send(monkeypatch, tmp_path):
    monkeypatch.setenv("QD_V3_RECORD", "true")
    journal = Journal(tmp_path/"journal", dsn="")
    ctx, read, plan = desk()
    read["bars"] = Feed(Snapshot({}, {"synthetic":True}, ctx.now))
    sig = signal(ctx, read, plan, Feed(), [], "", {})
    assert sig["preview"] and record(journal, ctx, read, {**plan,"movement_v3":sig}) is None
    assert price_alert(journal, ctx, read) is None
    import monitor_engine
    monkeypatch.setattr(monitor_engine, "build_decision", lambda *a,**k: (dict(plan), {}))
    alerts, result = process_scan(journal, ctx, read)
    assert alerts == [] and result["demo"]
    # An accidental path into the worker with a nested preview still fails before a provider or webhook.
    display = {"status":"ENTER LONG","movement_v3":sig}
    trade = {"plan":display, "symbol":"SPY", "origin":ctx.now.isoformat()}
    attempt_entry(trade, ctx, None, False)
    assert trade["state"] == "SKIPPED" and "Synthetic" in trade["reason"]
    deliver(None, None, None, "unused", display)
    assert not journal.records("SPY", "v3_scan")


def test_historical_preview_is_excluded_from_reports_models_and_export(monkeypatch, tmp_path):
    monkeypatch.setenv("QD_V3_RECORD", "true")
    j = Journal(tmp_path/"journal", dsn="")
    contaminated = fake_records(25)
    for i,row in enumerate(contaminated):
        if i%3 == 0:
            row["payload"]["signal"]["preview"] = True
        elif i%3 == 1:
            row["payload"]["signal"]["source"] = "Sandbox"
        else:
            row["payload"]["synthetic"] = True
        j.insert(row["id"],"SPY","v3_scan",str(DAY),row["origin"],row["payload"])
    assert market_events(contaminated,[])["eligible_origins"] == 0
    assert matured(contaminated) == [] and training_records(j,"SPY",str(DAY)) == []
    daily(j, CLOSE+timedelta(minutes=16))
    assert j.records("DESK","v3_report",scope=str(DAY)) == []
    live = fake_records(1)[0]
    live["id"] = "good"
    assert archive([*contaminated,live],tmp_path,"SPY",DAY) == 1
    assert [r["id"] for r in archive_rows(tmp_path/f"{DAY}-SPY.parquet")] == ["good"]


def test_invalidation_is_shared_and_scoped(monkeypatch,tmp_path):
    monkeypatch.setenv("QD_V3_RECORD","true")
    monkeypatch.setenv("QD_V3_DISCORD","true")
    ctx,read,plan = desk()
    j = Journal(tmp_path/"j",dsn="")
    old = signal(ctx,read,plan,Feed(),[],"",{})
    assert old["state"] == "BULLISH IGNITION"
    old["invalidation"] = 99
    previous = deepcopy(old)
    key, alert = transition(j, ctx, {"movement_v3":old}, "prior")
    assert alert["version"] == old["version"] and alert["status"] == "V3 WATCH"
    current = {**old,"at":(ctx.now+MINUTE).isoformat(),"price":98,"state":"BEARISH IGNITION"}
    resolved = resolve_state(current,prior_state(j,ctx,current))
    assert resolved["state"] == "INVALIDATED" and resolved["prior_invalidation"] == 99
    assert old == previous
    assert resolve_state(current, prior_state(j,ctx,{**current,"source":"Schwab"}))["state"] == "BEARISH IGNITION"


def test_research_selects_the_configured_build_and_mode(monkeypatch, tmp_path):
    from v3_evaluate import evaluate
    from v3_feedback import parameters
    from v3_movement import VERSION
    monkeypatch.setenv("QD_V3_MODE", "Conservative")
    monkeypatch.setenv("QD_V3_RECORD", "true")
    j = Journal(tmp_path/"journal", dsn="")
    entries = fake_records(3)
    entries[1]["payload"]["signal"]["version"] = "previous"
    entries[2]["payload"]["signal"]["mode"] = "Balanced"
    for row in entries:
        j.insert(row["id"],"SPY","v3_scan",str(DAY),row["origin"],row["payload"])
    assert [r["id"] for r in training_records(j,"SPY",str(DAY))] == ["0"]
    assert parameters()["ignition_threshold"] == 75
    assert archive(entries,tmp_path,"SPY",DAY) == 3
    assert evaluate(tmp_path,"SPY")["days"][0]["recorded"] == 1
    assert evaluate(tmp_path,"SPY",version="previous")["days"][0]["recorded"] == 1
    daily(j,CLOSE+timedelta(minutes=16))
    summary = j.records("DESK","v3_report",scope=str(DAY))[0]["payload"]
    assert summary["research_config"] == {"version":VERSION,"mode":"Conservative"}
    assert summary["SPY"]["recorded"] == 1


@pytest.mark.parametrize("sandbox", [True,False])
def test_webull_footprint_and_futures_mark_the_environment(sandbox):
    from webull_feed import WebullFeedStore
    from v3_provenance import simulated_feed
    market = SimpleNamespace(get_footprint=None)
    futures = SimpleNamespace(get_futures_history_bars=None, get_futures_footprint=None)
    instrument = SimpleNamespace(get_futures_instrument=None)
    def request(name,*args,**kwargs):
        if name == "Futures contracts":
            return [{"symbol":"ESZ6","code":"ES","status":"OC","last_trading_date":"2026-12-18"}]
        return {"symbol":kwargs["symbols"][0] if name=="Stock footprint" else "ESZ6",
                "delay_minutes":0,"result":[]}
    api = SimpleNamespace(request=request, data=SimpleNamespace(market_data=market, futures_market_data=futures,
                                                               instrument=instrument))
    host = "api.sandbox.webull.com" if sandbox else "api.webull.com"
    store = SimpleNamespace(_client=lambda:api,settings=SimpleNamespace(endpoint=host, category="US_ETF"),budget=None)
    stock = WebullFeedStore._read_footprint(store,"SPY")["SPY"]
    proxy = WebullFeedStore._read_futures(store,("ES",str(DAY)))["ES",str(DAY)]
    assert simulated_feed(Feed(stock)) is sandbox
    assert simulated_feed(Feed(proxy)) is sandbox
