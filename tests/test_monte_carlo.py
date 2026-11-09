import copy
import math
from datetime import timedelta
import numpy as np
import pytest
from scipy.stats import norm
from data_utils import utc
from monte_carlo import fit,forecast,simulate,summarize,return_segments,walk_forward,ForecastError
from quant_agent import greeks


def test_gbm_matches_analytic_distribution():
    sigma=.001;minutes=30
    samples=simulate({'sigma':sigma,'drift':0.},600.,minutes,paths=50000,model='gbm')
    log_return=np.log(samples[:,-1]/600)
    assert abs(log_return.mean())<.0001
    assert log_return.var()==pytest.approx(sigma*sigma*minutes,rel=.025)
    assert np.quantile(samples[:,-1],.975)==pytest.approx(600*math.exp(norm.ppf(.975)*sigma*math.sqrt(minutes)),rel=.001)


def test_ranges_probabilities_and_reproducibility(candles):
    asof=utc(candles[-1]['time_utc'])+timedelta(minutes=1)
    a=forecast(candles,600.,paths=5000,asof=asof)
    b=forecast(candles,600.,paths=5000,asof=asof)
    assert a==b
    for r in a['horizons']:
        assert r['p_up']+r['p_down']+r['p_neutral']==pytest.approx(1.)
        assert r['low_95']<r['low_68']<r['median']<r['high_68']<r['high_95']
    assert a['horizons'][-1]['high_95']-a['horizons'][-1]['low_95']>a['horizons'][0]['high_95']-a['horizons'][0]['low_95']
    assert a['log_drift_per_minute']==0


def test_no_overnight_or_gap_returns(candles):
    segments=return_segments(candles)
    assert [len(s) for s in segments]==[389,389,389]
    changed=copy.deepcopy(candles)
    for b in changed[390:]: b['close']*=2
    assert np.concatenate(return_segments(changed))==pytest.approx(np.concatenate(segments))
    assert sum(map(len,return_segments(candles[:300]+candles[301:])))==1165


def test_future_and_duplicate_candles_rejected(candles):
    with pytest.raises(ForecastError,match='unfinished'): fit(candles,asof=utc(candles[-1]['time_utc']))
    with pytest.raises(ForecastError,match='Duplicate'): fit(candles+[candles[-1]])


def test_flat_data_refuses_spurious_certainty(candles):
    changed=copy.deepcopy(candles)
    for b in changed: b['close']=600.
    with pytest.raises(ForecastError,match='variability'): fit(changed)


def test_barrier_touch_and_deadband_are_distinct():
    samples=np.array([[100,101,100.1],[100,99,100],[100,100.5,100.4]],dtype=float)
    result=summarize(samples,100,2,deadband_bps=20,levels={'Upper':100.7,'Lower':99.5})
    assert result['p_up']==pytest.approx(1/3)
    assert result['p_neutral']==pytest.approx(2/3)
    assert result['levels'][0]['touch_probability']==pytest.approx(1/3)
    assert result['levels'][1]['touch_probability']==pytest.approx(1/3)


def test_walk_forward_does_not_leak_future(candles):
    subset=copy.deepcopy(candles[:390]);changed=copy.deepcopy(subset)
    for row in changed[241:]: row['close']*=1.10
    a=walk_forward(subset,horizon=15,model='gbm',paths=500)
    b=walk_forward(changed,horizon=15,model='gbm',paths=500)
    assert a['outcomes'][0]['p_up']==b['outcomes'][0]['p_up']
    assert a['outcomes'][0]['low_95']==b['outcomes'][0]['low_95']
    assert a['outcomes'][0]['actual']!=b['outcomes'][0]['actual']
    assert a['forecasts']==9


def test_greeks_against_finite_differences():
    s,k,t,iv,r,q=100.,103.,.13,.27,.04,.012
    def delta(spot,vol,tenor,right='C'):
        d1=(math.log(spot/k)+(r-q+.5*vol**2)*tenor)/(vol*math.sqrt(tenor))
        return math.exp(-q*tenor)*(norm.cdf(d1)-(1 if right=='P' else 0))
    gamma,vanna,disc,pdf,dd1dt,d1=greeks(s,k,t,iv,r,q)
    assert gamma==pytest.approx((delta(s+.001,iv,t)-delta(s-.001,iv,t))/.002,rel=1e-6)
    assert vanna==pytest.approx((delta(s,iv+.00001,t)-delta(s,iv-.00001,t))/.00002,rel=1e-6)
    for right in ('C','P'):
        actual=-(-q*delta(s,iv,t,right)+disc*pdf*dd1dt)/365.25
        dt=.001/365.25
        expected=(delta(s,iv,t-dt,right)-delta(s,iv,t+dt,right))/.002
        assert actual==pytest.approx(expected,rel=1e-6)
