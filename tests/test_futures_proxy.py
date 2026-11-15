from datetime import datetime, timezone

from futures_proxy import activity, choose_contract
from terminal_charts import integrated_intraday_figure


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


def test_spx_proxy_draws_contract_volume_without_moving_spx_price():
    now=datetime(2026,11,13,15,31,tzinfo=timezone.utc)
    candles=[{'time_utc':datetime(2026,11,13,15,i,tzinfo=timezone.utc).isoformat(),
              'open':7550+i,'high':7552+i,'low':7549+i,'close':7551+i,'volume':None} for i in (25,30)]
    fig=integrated_intraday_figure(candles,'SPX','5m',profile='GEX',
        proxy_points=[{'time':now.isoformat(),'volume':120,'cvd':30}],proxy_label='ES')
    assert any(t.name=='ES volume' for t in fig.data)
    assert any(t.name=='ES footprint CVD' for t in fig.data)
    assert all(t.name!='Volume profile' for t in fig.data)
    assert list(fig.data[0].close)==[7576,7581]
