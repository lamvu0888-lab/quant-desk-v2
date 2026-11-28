"""Regression checks for option lifetime curves, with fixed ETF prices and known analytic limits."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from scipy.stats import norm

from v3_options import YEAR, Spec, greeks, scenario, value


NOW = datetime(2026, 11, 27, 16, tzinfo=timezone.utc)


def contract(symbol, right, strike, american=True):
    close = NOW + timedelta(minutes=390)
    return Spec(symbol, strike, right, american, close, close, 'Close')


def european_delta_gamma(spec, spot, years, sigma, rate, dividend):
    deviation = sigma * np.sqrt(years)
    d1 = (np.log(spot / spec.strike) + (rate - dividend + sigma**2 / 2) * years) / deviation
    discount = np.exp(-dividend * years)
    delta = discount * (norm.cdf(d1) if spec.right == 'call' else -norm.sf(d1))
    return delta, discount * norm.pdf(d1) / (spot * deviation)


@pytest.mark.parametrize('symbol,right,american,rate,dividend', [
    ('SPY', 'call', True, .04, 0.), ('QQQ', 'put', True, 0., .01),
    ('SPXW', 'call', False, .04, .012), ('SPXW', 'put', False, .04, .012),
])
def test_no_early_exercise_matches_closed_form_across_maturities(symbol, right, american, rate, dividend):
    spec = contract(symbol, right, 700, american)
    years = np.array([1 / 60, 1, 5, 60, 390, 7 * 24 * 60]) * 60 / YEAR
    spot = np.array([699.9, 700., 700.1])[:, None]
    data = greeks(spec, spot, years, .22, rate, dividend)
    delta, gamma = european_delta_gamma(spec, spot, years, .22, rate, dividend)
    np.testing.assert_allclose(data['delta'], delta, atol=1e-11, rtol=1e-11)
    np.testing.assert_allclose(data['gamma'], gamma, atol=1e-11, rtol=1e-11)


@pytest.mark.parametrize('symbol,right,strike,spot,iv,dividend', [
    ('SPY', 'call', 700, 699, .22, .012), ('SPY', 'put', 700, 699, .22, .012),
    ('QQQ', 'call', 600, 601, .28, .005), ('QQQ', 'put', 600, 601, .28, .005),
])
def test_flat_etf_scenario_has_smooth_intraday_greeks(symbol, right, strike, spot, iv, dividend):
    spec = contract(symbol, right, strike)
    # Fixed near-the-money spot and IV isolate the effect of time passing. In this interval the
    # early-exercise premium is small. European Delta/Gamma provide an independent approximation.
    times = [NOW + timedelta(minutes=n) for n in range(371)] + [spec.expires]
    _, remaining, data = scenario(spec, NOW, times, spot, spot, times[1], iv, iv, .04, dividend)
    delta, gamma = european_delta_gamma(spec, spot, remaining[:-1], iv, .04, dividend)
    np.testing.assert_allclose(data['gamma'][:-1], gamma, atol=.002, rtol=.002)
    np.testing.assert_allclose(data['delta'][:-1], delta, atol=.004, rtol=.002)
    assert np.max(np.abs(np.diff(data['gamma'][:-1]))) < .003
    assert np.isnan(data['gamma'][-1]) and np.isnan(data['delta'][-1])
    sign = 1 if right == 'call' else -1
    assert data['premium'][-1] == max(sign * (spot - strike), 0.)


@pytest.mark.parametrize('symbol,right,spot,strike,rate,dividend,expected_delta', [
    ('SPY', 'call', 150, 120, .01, .12, 1.), ('QQQ', 'put', 500, 600, .05, .005, -1.),
])
def test_immediate_exercise_region_uses_intrinsic_and_correct_local_derivatives(
    symbol, right, spot, strike, rate, dividend, expected_delta,
):
    spec = contract(symbol, right, strike)
    years = 10 / 365.25
    data = greeks(spec, spot, years, .2, rate, dividend, extended=True)
    assert value(spec, spot, years, .2, rate, dividend) == data['premium'] == abs(spot - strike)
    assert data['delta'] == expected_delta
    assert data['gamma'] == 0.
    assert data['vanna'] == data['charm'] == 0.
