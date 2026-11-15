from datetime import datetime, timezone

from futures_proxy import activity, choose_contract, recent_pressure
from datetime import timedelta
from terminal_charts import futures_activity_figure


def test_contract_roll_and_cvd_entitlement():
    assert choose_contract([{'symbol':'ESZ6','code':'ES','status':'OC','last_trading_date':'2026-12-18'},
                            {'symbol':'ESH7','code':'ES','status':'OC','last_trading_date':'2027-03-19'}],
                           'ES','2026-11-13') == 'ESZ6'
    now=datetime(2026,11,13,15,32,tzinfo=timezone.utc)
    bars={'result':[{'symbol':'ESZ6','delay_minutes':0,'result':[
        {'time':'2026-11-13T15:29:00+00:00','volume':'100'},
        {'time':'2026-11-13T15:30:00+00:00','volume':'120'}]}]}
    footprint={'result':[{'symbol':'ESZ6','delay_minutes':0,'result':[
        {'time':'2026-11-13T15:29:00+00:00','buy_total':'60','sell_total':'40'},
        {'time':'2026-11-13T15:30:00+00:00','buy_total':'45','sell_total':'75'}]}]}
    points=activity(bars,footprint,'ESZ6',now,'2026-11-13')
    assert [p['volume'] for p in points]==[100,120]
    assert [p['cvd'] for p in points]==[20,-10]
    assert all(p['cvd'] is None for p in activity(bars,None,'ESZ6',now,'2026-11-13'))
    footprint['result'][0]['delay_minutes']=15
    assert all(p['cvd'] is None for p in activity(bars,footprint,'ESZ6',now,'2026-11-13'))


def test_es_proxy_draws_only_returned_contract_data():
    now = datetime(2026, 11, 13, 15, 31, tzinfo=timezone.utc)
    point = {"time": now.isoformat(), "volume": 120, "cvd": 30}
    fig = futures_activity_figure([point], "ESZ6")
    assert [trace.name for trace in fig.data] == ["ESZ6 volume", "ESZ6 footprint CVD"]
    volume_only = futures_activity_figure([{**point, "cvd": None}], "ESZ6")
    assert [trace.name for trace in volume_only.data] == ["ESZ6 volume"]


def test_pressure_requires_ten_matching_observed_footprint_minutes():
    end = datetime(2026, 11, 13, 15, 40, tzinfo=timezone.utc)
    rows = [{"time": (end - timedelta(minutes=9-i)).isoformat(), "volume": 100,
             "delta": 10, "cvd": (i+1)*10} for i in range(10)]
    assert recent_pressure(rows, end.isoformat()) == {"delta": 100, "volume": 1000,
                                                       "count": 10, "close": end.isoformat()}
    assert recent_pressure(rows, (end - timedelta(minutes=1)).isoformat()) is None
    assert recent_pressure([{**rows[0], "delta": None}, *rows[1:]], end.isoformat()) is None
    assert recent_pressure([*rows[:4], {**rows[4], "time": rows[3]["time"]}, *rows[5:]], end.isoformat()) is None
