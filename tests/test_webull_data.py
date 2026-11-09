from datetime import datetime,timedelta,timezone
from dataclasses import replace
from unittest.mock import Mock
import math
import pytest
from data_utils import DataError,reported_delay,records,BudgetPause,safe_error
from my_webull_adapter import normalize_quote,normalize_candles,aggregate_candles,WebullSDKAdapter
from live_engine import data_quality
from settings import Settings
from rate_control import RequestBudget
from market_session import session_state,rth_bounds

UTC=timezone.utc
NOW=datetime(2026,11,3,17,0,tzinfo=UTC)


def sample_state(candles):
    q={'symbol':'SPY','price':'601.25','bid':'601.24','ask':'601.26',
       'quote_time':int(NOW.timestamp()*1000),'last_trade_time':int(NOW.timestamp()*1000)}
    history=[b for b in candles if b['time_utc']<(NOW-timedelta(seconds=30)).isoformat()]
    return {'mode':'PRODUCTION','running':True,'quote':normalize_quote([q],'SPY',now=NOW),
            'history':{'candles':history,'last_bar_utc':(NOW-timedelta(minutes=1)).isoformat(),
                       'delay_minutes':0,'received_utc':NOW.isoformat()}}


def test_nested_real_webull_bar_shape_and_delay():
    raw={'result':[{'symbol':'SPY','delay_minutes':0,'result':[
        {'time':'2026-11-03T16:59:00.000+0000','open':'601','high':'602','low':'600','close':'601.3','volume':'500'}]}]}
    out=normalize_candles(raw,'SPY',now=NOW)
    assert out['delay_minutes']==0 and len(out['candles'])==1
    assert out['candles'][0]['close']==601.3


def test_missing_delay_not_fabricated():
    assert reported_delay([{'symbol':'SPY','price':'600'}],'SPY') is None
    assert reported_delay({'result':[{'symbol':'QQQ','delay_minutes':0}]},'SPY') is None


def test_unfinished_bar_removed():
    raw=[{'time':'2026-11-03T16:59:00Z','open':1,'high':1,'low':1,'close':1,'volume':0},
         {'time':'2026-11-03T17:00:00Z','open':1,'high':1,'low':1,'close':1,'volume':0}]
    assert len(normalize_candles(raw,'SPY',now=NOW)['candles'])==1


def test_nan_prices_rejected():
    with pytest.raises(DataError):
        normalize_quote([{'symbol':'SPY','price':'nan','quote_time':NOW.isoformat()}],'SPY',now=NOW)


def test_timestamp_without_timezone_rejected():
    with pytest.raises(DataError): normalize_quote([{'symbol':'SPY','price':600,'quote_time':'2026-11-03T17:00:00'}],'SPY',now=NOW)


def test_feed_entitlement_evidence_from_bars(candles):
    q=data_quality(sample_state(candles),Settings(),now=NOW)
    assert q['forecast_ready'] and q['delay_minutes']==0
    assert q['delay_source'].startswith('Webull one-minute bars')


def test_explicit_delayed_snapshot_overrides_zero_delay_bars(candles):
    state=sample_state(candles);state['quote']['delay_minutes']=15
    assert not data_quality(state,Settings(),now=NOW)['forecast_ready']


def test_stale_polling_and_pause_block_forecast(candles):
    state=sample_state(candles)
    assert not data_quality(state,Settings(),now=NOW+timedelta(seconds=31))['forecast_ready']
    state['running']=False
    assert not data_quality(state,Settings(),now=NOW)['forecast_ready']


def test_unknown_candle_delay_blocks_forecast(candles):
    state=sample_state(candles);state['quote']['delay_minutes']=0;state['history']['delay_minutes']=None
    assert not data_quality(state,Settings(),now=NOW)['forecast_ready']


def test_fresh_wrapper_cannot_refresh_old_trade(candles):
    state=sample_state(candles);state['quote']['price_time_utc']=(NOW-timedelta(hours=1)).isoformat()
    state['quote']['midpoint']=None
    assert not data_quality(state,Settings(),now=NOW)['forecast_ready']
    state['quote']['midpoint']=601.25
    q=data_quality(state,Settings(),now=NOW)
    assert q['forecast_ready'] and q['price_kind']=='Bid/ask midpoint'


def test_option_failure_does_not_block_stock_forecast(candles):
    state=sample_state(candles);state['chain_error']='HTTP 403'
    assert data_quality(state,Settings(),now=NOW)['forecast_ready']
    assert not data_quality(state,Settings(),now=NOW)['options_live']


def test_rth_aggregation_aligns_open_and_drops_gaps(candles):
    rows=candles[:20]
    assert len(aggregate_candles(rows,5))==4
    assert len(aggregate_candles(rows[1:],5))==3
    assert len(aggregate_candles(rows[:7]+rows[8:],5))==3
    assert aggregate_candles(rows[1:],5)[0]['time_utc']==rows[5]['time_utc']


def test_market_holidays_early_close_and_dst():
    assert session_state(datetime(2026,11,26,17,tzinfo=UTC))['status']=='CLOSED'
    s=session_state(datetime(2026,11,27,17,30,tzinfo=UTC))
    assert s['minutes_left']==30 and s['status']=='OPEN'
    assert session_state(datetime(2026,11,27,18,tzinfo=UTC))['status']=='CLOSED'
    assert rth_bounds('2026-10-30')[0].hour==13
    assert rth_bounds('2026-11-02')[0].hour==14


def test_rate_limiter_nonblocking_and_cooldown():
    clock=[0.];budget=RequestBudget(2,clock=lambda:clock[0]);budget.acquire()
    with pytest.raises(BudgetPause): budget.acquire()
    clock[0]=2;budget.acquire();clock[0]=4
    with pytest.raises(BudgetPause) as e: budget.acquire()
    assert e.value.seconds>50
    clock[0]=61;budget.cooldown(70)
    with pytest.raises(BudgetPause) as e: budget.acquire()
    assert e.value.seconds==70


def test_sdk_raises_http429_still_cools_down():
    from webull.core.exception.exceptions import ServerException
    adapter=WebullSDKAdapter(Settings(),data=Mock());budget=RequestBudget()
    method=Mock(side_effect=ServerException('TOO_MANY_REQUESTS','sensitive request omitted',429))
    with pytest.raises(BudgetPause): adapter.request('Stock snapshot',method,budget)
    assert budget.status()['cooldown_seconds']>50


def test_sdk_exception_redacted():
    class SDKException(Exception): pass
    assert 'secret_value' not in safe_error(SDKException('secret_value'))


def test_all_modules_have_no_order_submission():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    for name in ('dashboard.py','live_engine.py','my_webull_adapter.py','live_chain.py','monte_carlo.py','quant_agent.py'):
        source=(root/name).read_text()
        for forbidden in ('place_order(', 'submit_order(', 'get_trade_token(', 'cancel_order(', 'TradeClient('):
            assert forbidden not in source
