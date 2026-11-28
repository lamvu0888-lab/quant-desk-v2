from datetime import date, timedelta
from types import SimpleNamespace

from market_session import rth_bounds
from movement_charts_v3 import MINUTE, movement_figure, preview_history, price_bars, scan_history
from v3_movement import VERSION


def fixture():
    day = date(2026, 11, 27)
    start, _ = rth_bounds(day)
    now = start + 13 * MINUTE + timedelta(seconds=2)
    ctx = SimpleNamespace(ticker="SPX", session=day, scope="0 DTE", now=now, preview=False, expiry=str(day))
    bars = []
    records = []
    for i in range(13):
        stamp = start + i * MINUTE
        close = stamp + MINUTE
        bar = dict(time_utc=stamp.isoformat(), open=99.5+i, high=101+i, low=99+i, close=100+i, volume=100)
        bars.append(bar)
        signal = dict(at=close.isoformat(), processed=(close+timedelta(seconds=1)).isoformat(), source="Schwab",
                      price=100+i, mode="Balanced", version=VERSION, preview=False, session=str(day), symbol="SPX",
                      scores={"bullish":60, "bearish":25, "ignition":59, "readiness":66}, forecasts={"5":{"lower":99,"upper":105}},
                      state="BUILDING" if i < 5 else "BULLISH PRESSURE", features={"gex":-100,"net_change":10,"net_acceleration":4})
        records.append(dict(origin=signal["processed"], payload=dict(ohlcv=bar, signal=signal, scope=ctx.scope,
                                                                     decision="ENTER LONG" if i==8 else "WAIT")))
    return ctx, {"movement_v3":signal}, {"candles":bars, "bars_ready":True}, records


def test_scope_provenance_clock_and_chart_gaps():
    ctx, plan, read, records=fixture()
    for idx, fields in enumerate(({"symbol":"QQQ"},{"preview":True},{"mode":"Aggressive"},
                                  {"source":"Webull"},{"version":"old"},{"session":"2026-11-25"})):
        records[idx]["payload"]["signal"].update(fields)
    records[6]["payload"]["scope"]="other"
    records[7]["origin"]=(ctx.now+MINUTE).isoformat()
    records[8]["payload"]["synthetic"]=True
    hist=scan_history(ctx,plan,records)
    assert len(hist)==4
    end=list(hist)[0]
    hist.pop(end+MINUTE)
    fig=movement_figure(ctx,plan,read,hist,1,"Netflow changes")
    traces={t.name:t for t in fig.data}
    assert traces["Bull pressure"].y[-3] is None
    assert {"Netflow acceleration","Movement state","Upper 5m barrier"}.issubset(traces)
    assert all(trace.xaxis=="x" for trace in fig.data)
    ctx.preview=True
    assert not scan_history(ctx,plan,records)


def test_intervals_omit_partial_missing_and_forming_minutes():
    ctx,plan,read,records=fixture()
    hist=scan_history(ctx,plan,records)
    fig=movement_figure(ctx,plan,read,hist,3)
    traces={t.name:t for t in fig.data}
    assert len(traces["3m price"].x)==4 and list(traces["3m price"].close)==[102,105,108,111]
    assert "Qualified plan" in traces
    read["candles"][5]["forming"]=True
    assert len(price_bars(ctx,read,{},3))==3 and len(price_bars(ctx,read,{},5))==1


def test_preview_price_replay_records_no_entries_or_flow():
    ctx,plan,read,records=fixture()
    replay=preview_history(read["candles"],ctx.ticker,ctx.session,ctx.expiry,"Preview","Balanced",VERSION)
    assert replay and all(p["signal"]["preview"] for p in replay.values())
    assert all("decision" not in p and p["signal"]["features"]["net_pressure"] is None for p in replay.values())


def test_provisional_and_invalidation_histories_are_typed_and_scoped():
    from movement_charts_v3 import lifecycle_extras
    ctx, plan, read, records = fixture()
    base = plan["movement_v3"]
    def history(symbol, kind, *, scope, limit):
        assert symbol == "SPX"
        if kind == "v3_transition":
            assert scope == f"{ctx.session}|{ctx.scope}"
            marker = dict(base, state="INVALIDATED")
            return [{"payload":marker}, {"payload":dict(marker, mode="Aggressive")}, {"payload":dict(marker, preview=True)}]
        assert scope == f"{ctx.session}|Schwab"
        marker = dict(base, state="PROVISIONAL IMPULSE")
        return [{"payload":marker}, {"payload":dict(marker,version="old")}, {"payload":dict(marker,synthetic=True)}]
    ctx.journal = SimpleNamespace(records=history)
    assert [r[-1] for r in lifecycle_extras(ctx,base)] == ["INVALIDATED","PROVISIONAL IMPULSE"]
    ctx.preview = True
    assert lifecycle_extras(ctx,base) == []
