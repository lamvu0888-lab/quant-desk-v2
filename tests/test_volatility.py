import pytest
from volatility import demo_candles, analyze_volatility, CandleError

def test_demo_volatility():
    result=analyze_volatility(demo_candles('SPY'))
    assert result['status'] in ('COMPRESSION','EXPANSION','BREAKOUT WATCH','NEUTRAL')
    assert 0<=result['bb_width_percentile']<=100
    assert result['atr_ratio']>0

def test_reject_short_series():
    with pytest.raises(CandleError): analyze_volatility(demo_candles('SPY',30))

def test_reject_bad_bar():
    bars=demo_candles('SPY');bars[-1]['high']=0
    with pytest.raises(CandleError): analyze_volatility(bars)
