"""Regression coverage for the ordered detector and high-signal worker."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from streamlit.testing.v1 import AppTest

from decision_engine import ENTRY_STATES, make_trade_plan
from discord_monitor import data_warning, deliver
from monitor_engine import process_scan
from movement_detector import CHECKS, ENTRY_COOLDOWN, price_movement
from research_journal import Journal
from signal_explain import discord_text
from terminal_demo import DEMO_AT
from test_v25 import synthetic_inputs
from tradytics_feed import Feed


def test_flow_confirmation_uses_two_matching_intervals():
    inputs = synthetic_inputs()
    first = make_trade_plan(**inputs)
    assert first["status"] in ENTRY_STATES
    assert first["movement"]["closed_bars"] == 2
    assert first["flow_confirmation"]["count"] == 2

    changed = deepcopy(inputs)
    changed["flow_rows"][-2]["net_premium_usd"] = -5_000
    plan = make_trade_plan(**changed)
    assert plan["flow"]["direction"] == 1
    assert plan["flow_confirmation"]["count"] == 1
    assert plan["status"] not in ENTRY_STATES

    changed["flow_rows"].pop()
    plan = make_trade_plan(**changed)
    assert not plan["flow_confirmation"]["intervals"]
    assert plan["status"] not in ENTRY_STATES


def test_sideways_and_compression_keep_directional_entries_pending():
    inputs = synthetic_inputs()
    bars = deepcopy(inputs["buckets"][-7:])
    for row, close in zip(bars, (100, 100.1, 99.9, 100.07, 99.94, 100.02, 100.10)):
        row.update(high=close + .10, low=close - .10, close=close, volume=1_000)
    read = price_movement(bars, bars, 1, 1, .5, .03, "Normal", 0, None)
    assert read["market"] == "SIDEWAYS" and read["range_hold"]
    assert read["playbook"] == "Range mean reversion"
    assert not price_movement(bars, bars, 1, -1, .5, .03, "Normal", 0, None)["price_confirmed"]

    inputs["regime"]["label"] = "Compressing"
    for row in inputs["buckets"]:
        row["volume"] = 5_000
    held = make_trade_plan(**inputs)
    assert held["movement"]["boundary_hold"]
    assert held["movement"]["range_hold"]
    assert held["status"] not in ENTRY_STATES


def test_atomic_cooldown_applies_per_ticker_and_setup(tmp_path):
    path = tmp_path / "journal.sqlite3"
    a, b = Journal(path, dsn=""), Journal(path, dsn="")
    now = DEMO_AT
    setup = {"plan": {"setup": "Breakout close"}}

    def record(journal, ident, symbol, at, body=setup):
        return journal.record_candidate(ident, symbol, "Tradytics:0", at, body,
                                        alert_id=f"alert-{ident}", cooldown=ENTRY_COOLDOWN)

    assert record(a, "one", "SPY", now) == (True, True)
    assert record(b, "two", "SPY", now + timedelta(minutes=9, seconds=59)) == (True, False)
    assert record(b, "three", "SPY", now + ENTRY_COOLDOWN) == (True, True)
    assert record(b, "four", "SPX", now + timedelta(minutes=1)) == (True, True)
    assert record(a, "five", "SPY", now + timedelta(minutes=1),
                  {"plan": {"setup": "EMA 8/10 pullback"}}) == (True, True)
    assert record(b, "three", "SPY", now + 2 * ENTRY_COOLDOWN) == (False, False)
    assert not next(row for row in a.records("SPY", "candidate") if row["id"] == "two")["payload"]["entry_alert_id"]


def test_worker_keeps_watch_states_in_history_without_discord(tmp_path, monkeypatch):
    inputs = synthetic_inputs()
    ctx = SimpleNamespace(now=inputs["now"], ticker="SPY", session=inputs["session"])
    plan = make_trade_plan(**inputs)
    plan.update(symbol="SPY", data_reasons=[], price_context_ready=False, dealer_note="Gamma unavailable")
    journal = Journal(tmp_path / "watch.sqlite3", dsn="")
    monkeypatch.setattr("monitor_engine.build_decision", lambda *_: (plan, {}))
    read = {**inputs, "bars": Feed(), "gamma_feed": Feed()}
    for status in ("GET READY", "WAIT", "AVOID"):
        plan["status"] = status
        assert all(alert[1]["status"] == "MOVE WATCH" for alert in process_scan(journal, ctx, read)[0])
        ctx.now += timedelta(seconds=30)
    assert {row["payload"]["status"] for row in journal.records("SPY", "decision")} == {"GET READY", "WAIT", "AVOID"}


def test_exit_delivery_requires_its_entry_to_have_been_sent(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "delivery.sqlite3", dsn="")
    shared, send = Mock(configured=False), Mock()
    monkeypatch.setattr("discord_monitor.send", send)
    signal = {"symbol": "SPY", "status": "EXIT", "entry_alert_id": "entry", "triggered_at_utc": DEMO_AT.isoformat()}
    webhook = "https://discord.com/api/webhooks/123/test"
    deliver(journal, shared, webhook, "exit", signal)
    send.assert_not_called()
    assert journal.claim_delivery("entry")
    journal.delivery_state("entry", "sent")
    deliver(journal, shared, webhook, "exit", signal)
    send.assert_called_once_with(webhook, signal)


def test_quality_is_never_delivered_and_move_message_is_brief(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "quiet.sqlite3", dsn="")
    send = Mock()
    monkeypatch.setattr("discord_monitor.send", send)
    deliver(journal, Mock(configured=False), "https://discord.com/api/webhooks/123/test", "old-quality",
            {"symbol": "SPY", "status": "DATA QUALITY"})
    send.assert_not_called()
    message = discord_text({"symbol": "SPY", "status": "MOVE WATCH", "direction": "UP", "window": "5m",
                            "change_pct": .3, "previous_price": 750, "price": 752.25,
                            "bar": DEMO_AT.isoformat()})
    assert len(message.splitlines()) == 3 and "Price only" in message


def test_warning_rearms_after_two_healthy_scans():
    state = (0, 0, False)
    notices = []
    for bad in [True] * 6 + [False] + [True] * 4 + [False, False] + [True] * 3:
        state, notify = data_warning(state, bad)
        notices.append(notify)
    assert [i for i, notified in enumerate(notices) if notified] == [2, 15]


def test_movement_navigation_and_spx_proxy(monkeypatch):
    monkeypatch.setattr("dotenv.dotenv_values", lambda *args, **kwargs: {})
    for name in ("TRADYTICS_API_KEY", "WEBULL_APP_KEY", "WEBULL_APP_SECRET", "DISCORD_WEBHOOK_URL", "DEFAULT_TICKER"):
        monkeypatch.delenv(name, raising=False)
    path = Path(__file__).resolve().parents[1] / "terminal.py"
    app = AppTest.from_file(str(path), default_timeout=30).run()
    app.button(key="overview_open_movement").click().run()
    headings = [row.value for row in app.markdown if '<section class="qd-movement-stage">' in row.value]
    assert len(headings) == len(CHECKS)
    assert all(f"<h3>{name}</h3>" in row for name, row in zip(CHECKS, headings))
    assert len(app.get("plotly_chart")) >= len(CHECKS)  # separate observed-IV chart
    assert len([row for row in app.markdown if '<div class="qd-next-cue">' in row.value]) == len(CHECKS)
    app.segmented_control(key="movement_flow_interval").set_value("1m").run()
    assert not app.exception
    app.segmented_control(key="movement_iv_window").set_value(5).run()
    assert not app.exception
    app.button(key="watch_SPX").click().run()
    app.toggle(key="movement_spy_cvd").set_value(True).run()
    assert any("SPY estimated 10m CVD" in row.value for row in app.caption)
    app.button(key="movement_open_trade").click().run()
    app.button(key="trade_open_lab").click().run()
    app.text_input(key="lab_search").set_value("TF02").run()
    assert app.selectbox(key="lab_review").value == "TF02"
    assert not app.exception
