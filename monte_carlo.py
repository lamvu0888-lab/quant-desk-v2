"""Short-horizon price distributions, not an oracle or a trading win rate.

Two physical-measure scenario models use observed RTH one-minute returns:
lognormal diffusion (GBM) and moving-block bootstrap. No OI sign or option IV
is treated as an observed directional price drift. No overnight returns are fit.
"""
from __future__ import annotations
import math
from datetime import datetime, timedelta, timezone
import numpy as np
from data_utils import DataError, number, utc
from market_session import ET


class ForecastError(DataError):
    pass


def return_segments(candles, *, asof=None):
    if not isinstance(candles, list) or len(candles) < 121:
        raise ForecastError('Need at least 121 closed one-minute candles')
    asof = utc(asof or datetime.now(timezone.utc))
    segments, current, previous = [], [], None
    for bar in candles:
        stamp = utc(bar.get('time_utc'))
        price = number(bar.get('close'), 'candle close', minimum=.000001)
        if stamp + timedelta(minutes=1) > asof or bar.get('closed', True) is not True:
            raise ForecastError('Forecast input contains an unfinished or future candle')
        if stamp.second or stamp.microsecond:
            raise ForecastError('One-minute candles must start on a whole minute')
        if previous and stamp <= previous[0]:
            raise ForecastError('Duplicate or unordered one-minute candle')
        contiguous = previous and (stamp - previous[0]).total_seconds() == 60 and stamp.astimezone(ET).date() == previous[0].astimezone(ET).date()
        if contiguous:
            current.append(math.log(price / previous[1]))
        else:
            if current:
                segments.append(np.asarray(current))
            current = []
        previous = stamp, price
    if current:
        segments.append(np.asarray(current))
    if sum(len(x) for x in segments) < 120:
        raise ForecastError('Need 120 valid same-session one-minute returns; gaps and overnight returns are excluded')
    return segments


def fit(candles, drift_mode='neutral', *, asof=None):
    if drift_mode not in ('neutral', 'recent_trend'):
        raise ForecastError('Unknown drift mode')
    segments = return_segments(candles, asof=asof)
    all_returns = np.concatenate(segments)
    recent = all_returns[-240:]
    weights = .97 ** np.arange(len(recent) - 1, -1, -1)
    weights /= weights.sum()
    weighted_mean = float(weights @ recent)
    variance = float(weights @ (recent - weighted_mean) ** 2) / float(1 - weights @ weights)
    sigma = math.sqrt(max(0, variance))
    if not math.isfinite(sigma) or sigma < 1e-8:
        raise ForecastError('Observed return variability is too small for a meaningful forecast')
    # A short observed trend is noisy. Its weight and cap are disclosed, not optimized.
    drift = 0.0 if drift_mode == 'neutral' else float(np.clip(.10 * np.mean(all_returns[-60:]), -.15 * sigma, .15 * sigma))
    return {'segments': segments, 'sigma': sigma, 'drift': drift,
            'n_returns': len(all_returns), 'recent_count': len(recent),
            'drift_mode': drift_mode, 'last_bar_utc': candles[-1]['time_utc']}


def simulate(fitted, spot, minutes, *, paths=10000, model='bootstrap', seed=731, block_size=5):
    spot = number(spot, 'forecast spot', minimum=.000001)
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= 120:
        raise ForecastError('Forecast horizon must be 1–120 minutes')
    if not isinstance(paths, int) or not 500 <= paths <= 50000:
        raise ForecastError('Monte Carlo paths must be 500–50000')
    if not isinstance(block_size, int) or not 1 <= block_size <= 20:
        raise ForecastError('Bootstrap block size must be 1–20 minutes')
    rng = np.random.default_rng(seed)
    sigma, drift = fitted['sigma'], fitted['drift']
    if model == 'gbm':
        increments = rng.normal(drift, sigma, (paths, minutes))
    elif model == 'bootstrap':
        # Never construct a block across a missing minute or a session boundary.
        blocks = [np.lib.stride_tricks.sliding_window_view(s, block_size) for s in fitted['segments'] if len(s) >= block_size]
        if not blocks:
            raise ForecastError('Not enough contiguous observations for a bootstrap block')
        blocks = np.concatenate(blocks)[-500:]
        base = np.concatenate(fitted['segments'])[-500:]
        std = float(np.std(base, ddof=1))
        if std < 1e-8:
            raise ForecastError('Bootstrap sample has insufficient variation')
        # Center the actual eligible blocks, then scale to current EWMA volatility.
        blocks = (blocks - float(blocks.mean())) * sigma / std + drift
        selected = rng.integers(0, len(blocks), size=(paths, math.ceil(minutes / block_size)))
        increments = blocks[selected].reshape(paths, -1)[:, :minutes]
    else:
        raise ForecastError('Model must be gbm or bootstrap')
    log_moves = np.cumsum(increments, axis=1)
    if not np.isfinite(log_moves).all() or np.max(np.abs(log_moves)) > 30:
        raise ForecastError('Forecast parameters produced unstable price paths')
    return np.column_stack((np.full(paths, spot), spot * np.exp(log_moves)))


def summarize(paths, spot, minutes, deadband_bps=2, levels=None):
    band = number(deadband_bps, 'neutral band', minimum=0)
    if band > 100:
        raise ForecastError('Neutral band cannot exceed 100 basis points')
    terminal = paths[:, minutes]
    threshold = spot * band / 10000
    up = float(np.mean(terminal > spot + threshold))
    down = float(np.mean(terminal < spot - threshold))
    quantiles = np.quantile(terminal, [.025, .16, .5, .84, .975])
    result = {'minutes': minutes, 'p_up': up, 'p_down': down, 'p_neutral': max(0., 1 - up - down),
              'p_finish_above_spot': float(np.mean(terminal > spot)), 'median': float(quantiles[2]),
              'low_68': float(quantiles[1]), 'high_68': float(quantiles[3]),
              'low_95': float(quantiles[0]), 'high_95': float(quantiles[4]),
              'mean': float(terminal.mean()), 'deadband_bps': band,
              'max_mc_standard_error_pct': 100 * .5 / math.sqrt(len(paths)), 'levels': []}
    for name, value in (levels or {}).items():
        if value is None:
            continue
        level = number(value, 'target level', minimum=.000001)
        touch = np.max(paths[:, :minutes + 1], axis=1) >= level if level > spot else np.min(paths[:, :minutes + 1], axis=1) <= level
        result['levels'].append({'name': str(name), 'price': level, 'touch_probability': float(touch.mean())})
    return result


def forecast(candles, spot, *, horizons=(5, 15, 30, 60), paths=10000, model='bootstrap',
             drift_mode='neutral', seed=731, deadband_bps=2, levels=None, asof=None):
    if not horizons or any(not isinstance(x, int) or isinstance(x, bool) or not 1 <= x <= 120 for x in horizons):
        raise ForecastError('Invalid forecast horizons')
    horizons = sorted(set(horizons))
    fitted = fit(candles, drift_mode, asof=asof)
    samples = simulate(fitted, spot, max(horizons), paths=paths, model=model, seed=seed)
    qs = np.quantile(samples, [.025, .16, .5, .84, .975], axis=0)
    return {'status': 'MODEL_ESTIMATE', 'model': model, 'drift_mode': drift_mode, 'paths': paths, 'seed': seed,
            'spot': float(spot), 'asof_utc': utc(asof or datetime.now(timezone.utc)).isoformat(),
            'last_training_bar_utc': fitted['last_bar_utc'], 'training_returns': fitted['n_returns'],
            'sigma_per_minute': fitted['sigma'], 'realized_vol_annual_pct': fitted['sigma'] * math.sqrt(252 * 390) * 100,
            'log_drift_per_minute': fitted['drift'],
            'horizons': [summarize(samples, float(spot), m, deadband_bps, levels) for m in horizons],
            'fan': [{'minute': i, 'low_95': float(qs[0, i]), 'low_68': float(qs[1, i]), 'median': float(qs[2, i]),
                     'high_68': float(qs[3, i]), 'high_95': float(qs[4, i])} for i in range(max(horizons) + 1)],
            'sample_paths': samples[:35].tolist(), 'terminal_sample': samples[:, horizons[0]].tolist(),
            'probability_type': 'Uncalibrated conditional model probabilities; not trade win rates',
            'notes': ['Neutral mode sets expected log return to zero; it does not predict a direction.',
                      'Recent-trend mode uses 10% of the last 60-return mean, capped at ±0.15 minute sigma.',
                      'Bootstrap samples observed five-minute return blocks; GBM assumes Gaussian log returns.',
                      'Touch events are measured at one-minute checkpoints; intraminute touches may be missed.',
                      'News, jumps outside the observed sample and changes in market regime can invalidate ranges.',
                      'Gamma, Vanna and Charm are contextual scenarios and do not force a price drift.']}


def walk_forward(candles, *, horizon=15, model='bootstrap', drift_mode='neutral', paths=1500, window=240):
    """Nonoverlapping outcomes. Each forecast fits only bars available at its origin.

    This checks distribution calibration on a small recent sample, not strategy P&L.
    Zero returns receive no binary directional label and are reported separately.
    """
    if not isinstance(horizon, int) or not 1 <= horizon <= 60:
        raise ForecastError('Validation horizon must be 1–60 minutes')
    outcomes, flat, skipped = [], 0, 0
    for end in range(window, len(candles) - horizon, horizon):
        train = candles[max(0, end - window + 1):end + 1]
        origin = utc(candles[end]['time_utc'])
        target = utc(candles[end + horizon]['time_utc'])
        path = candles[end:end + horizon + 1]
        if (target - origin).total_seconds() != 60 * horizon or origin.astimezone(ET).date() != target.astimezone(ET).date():
            skipped += 1
            continue
        if any((utc(b['time_utc']) - utc(a['time_utc'])).total_seconds() != 60 for a, b in zip(path, path[1:])):
            skipped += 1
            continue
        spot, actual = float(candles[end]['close']), float(candles[end + horizon]['close'])
        try:
            fit_asof = origin + timedelta(minutes=1)
            fitted = fit(train, drift_mode, asof=fit_asof)
            sample = simulate(fitted, spot, horizon, paths=paths, model=model, seed=731 + end)
            s = summarize(sample, spot, horizon, deadband_bps=0)
        except ForecastError:
            skipped += 1
            continue
        direction = None if actual == spot else int(actual > spot)
        flat += int(direction is None)
        outcomes.append({'origin_utc': fit_asof.isoformat(), 'spot': spot, 'actual': actual,
                         'p_up': s['p_up'], 'low_68': s['low_68'], 'high_68': s['high_68'],
                         'low_95': s['low_95'], 'high_95': s['high_95'],
                         'inside_68': s['low_68'] <= actual <= s['high_68'],
                         'inside_95': s['low_95'] <= actual <= s['high_95'],
                         'brier': (s['p_up'] - direction) ** 2 if direction is not None else None})
    directional = [r['brier'] for r in outcomes if r['brier'] is not None]
    n = len(outcomes)
    return {'status': 'SMALL_SAMPLE' if n < 100 else 'RECENT_SAMPLE', 'forecasts': n, 'flat_outcomes': flat,
            'skipped_gaps_or_sessions': skipped, 'horizon': horizon, 'model': model, 'drift_mode': drift_mode,
            'coverage_68': float(np.mean([r['inside_68'] for r in outcomes])) if n else None,
            'coverage_95': float(np.mean([r['inside_95'] for r in outcomes])) if n else None,
            'brier_score': float(np.mean(directional)) if directional else None,
            'neutral_baseline_brier': .25 if directional else None, 'outcomes': outcomes,
            'note': 'Rolling forecasts use past data only and nonoverlapping outcomes. This recent sample does not establish directional skill or profitability.'}
