from datetime import datetime, timezone, timedelta
import math
import pytest
from quant_agent import demo,validate,exposures,greeks,DataError

def test_demo_and_exposure():
    p=demo('SPY')
    s=validate(p,'SPY',90,10,1)
    out=exposures(s)
    assert out['contracts']==16
    assert math.isfinite(out['net_gex_usd_per_1pct'])
    assert len(out['by_strike'])==8

def test_stale_quote_blocked():
    p=demo('SPY');p['quote_time_utc']=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
    with pytest.raises(DataError,match='Stale'):validate(p,'SPY',90,10,1)

def test_incomplete_chain_blocked():
    p=demo('SPY');p['chain_complete']=False
    with pytest.raises(DataError,match='completeness'):validate(p,'SPY',90,10,1)

def test_invalid_iv_blocked():
    p=demo('SPY');p['contracts'][0]['iv_decimal']=25
    with pytest.raises(DataError,match='malformed'):validate(p,'SPY',90,10,1)

def test_duplicate_blocked():
    p=demo('SPY');p['contracts'].append(dict(p['contracts'][0]))
    with pytest.raises(DataError,match='duplicate'):validate(p,'SPY',90,10,1)

def test_gamma_positive():
    gamma,*_=greeks(100,100,.1,.2,.04,0)
    assert gamma>0

def test_no_execution_calls():
    from pathlib import Path
    source=Path(__file__).resolve().parents[1].joinpath('quant_agent.py').read_text()
    for forbidden in ('place_order(', 'submit_order(', 'get_trade_token(', 'cancel_order('):
        assert forbidden not in source
