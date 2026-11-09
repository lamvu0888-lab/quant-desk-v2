"""Conservative, analysis-only volatility/Greek confirmation. No orders or trade advice."""
from __future__ import annotations
from datetime import datetime, timezone
import math


def _finite(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def analyze_confirmation(volatility: dict, exposure: dict, *, previous_volatility=None,
                         iv_change_pct_points=None, flow_direction=None, one_minute_candles=None):
    """Return descriptive evidence score (not win probability).

    `flow_direction` must be independently observed: 'BUY', 'SELL', or 'MIXED'.
    An IV change requires comparable ATM tenor/expiry IV observations; absent -> unknown.
    Optional 1m candles confirm direction using last three CLOSED bars, not intrabar data.
    """
    status=volatility.get('status','UNAVAILABLE')
    spot=_finite(exposure.get('spot'))
    gex=_finite(exposure.get('net_gex_usd_per_1pct'))
    vex=_finite(exposure.get('net_vex_delta_shares_per_vol_point'))
    charm=_finite(exposure.get('net_charm_delta_shares_per_day'))
    delta=_finite(exposure.get('net_delta_proxy_shares'))
    if status=='UNAVAILABLE' or spot is None or gex is None:
        return {'status':'UNAVAILABLE','direction':'UNKNOWN','confirmation_score':0,
                'reason':'Volatility or exposure data unavailable; signal suppressed', 'evidence':[]}
    evidence=[]
    direction='UNKNOWN'
    breakout_direction=volatility.get('breakout_direction','NONE')
    if status=='BREAKOUT WATCH':
        direction=breakout_direction if breakout_direction in ('UP','DOWN') else 'UNKNOWN'
        evidence.append('Prior compression and range boundary break detected on closed 5m bars')
    elif status=='EXPANSION':
        direction=volatility.get('price_direction','UNKNOWN')
        evidence.append('Expanding 5m volatility detected')
    elif status=='COMPRESSION':
        evidence.append('Compressed 5m volatility: direction not established')
    else:
        evidence.append('No confirmed compression or expansion')
    # Evidence points are transparent heuristic confirmations, not calibrated probabilities.
    score=0
    if status in ('BREAKOUT WATCH','EXPANSION'):
        score+=25
    elif status=='COMPRESSION':
        score+=15
    ratio=_finite(volatility.get('volume_ratio'))
    if ratio is not None and ratio>=1.5 and status in ('BREAKOUT WATCH','EXPANSION'):
        score+=15; evidence.append('Relative volume at least 1.5x')
    if gex<0 and status in ('BREAKOUT WATCH','EXPANSION'):
        score+=10; evidence.append('Negative modeled GEX: possible amplification if proxy matches dealer positioning')
    elif gex>=0:
        evidence.append('Nonnegative modeled GEX: potential damping, positioning unverified')
    if iv_change_pct_points is not None:
        iv=_finite(iv_change_pct_points)
        if iv is not None:
            evidence.append(f'Comparable-tenor ATM IV change: {iv:+.2f} percentage points')
            if iv>0 and status in ('BREAKOUT WATCH','EXPANSION'):
                score+=5
    else:
        evidence.append('ATM IV trend unavailable; Vanna directional confirmation withheld')
    if vex is not None:
        evidence.append(f'Modeled Vanna proxy {vex:+,.0f} delta-shares per IV point (not dealer inventory)')
    if charm is not None:
        evidence.append(f'Modeled Charm proxy {charm:+,.0f} delta-shares/day; time-decay hedge direction unverified')
    if delta is not None:
        evidence.append(f'Modeled delta proxy {delta:+,.0f} shares; not observed dealer hedge flow')
    if flow_direction in ('BUY','SELL','MIXED'):
        if direction in ('UP','DOWN') and flow_direction==('BUY' if direction=='UP' else 'SELL'):
            score+=20; evidence.append('Independent order flow agrees with price direction')
        else:
            evidence.append('Order flow does not confirm directional move')
    else:
        evidence.append('True buy/sell order flow unavailable; no directional flow credit')
    if one_minute_candles is not None and direction in ('UP','DOWN'):
        try:
            bars=one_minute_candles
            if len(bars)>=4 and all(bool(b.get('closed',False)) for b in bars[-4:]):
                c=[float(b['close']) for b in bars[-4:]]
                if all(math.isfinite(v) and v>0 for v in c):
                    confirmed=(all(c[i]>c[i-1] for i in range(1,4)) if direction=='UP'
                               else all(c[i]<c[i-1] for i in range(1,4)))
                    if confirmed:
                        score+=15; evidence.append('Three consecutive closed 1m bars confirm direction')
                    else: evidence.append('1m direction confirmation absent')
            else: evidence.append('Insufficient explicitly closed 1m bars')
        except (ValueError, TypeError, KeyError):
            evidence.append('Invalid 1m candles; confirmation withheld')
    if status=='BREAKOUT WATCH' and previous_volatility and previous_volatility.get('status')=='BREAKOUT WATCH':
        if previous_volatility.get('breakout_direction')!=breakout_direction:
            evidence.append('Breakout direction changed: possible failed breakout')
            status='FAILED BREAKOUT'; direction='UNKNOWN'; score=min(score,30)
    if status=='EXPANSION': status='EXPANSION UP' if direction=='UP' else 'EXPANSION DOWN' if direction=='DOWN' else 'EXPANSION'
    if status=='BREAKOUT WATCH' and direction=='UNKNOWN':
        evidence.append('Breakout direction unavailable: directional alert suppressed')
    return {'status':status,'direction':direction,'confirmation_score':min(score,100),
            'score_type':'uncalibrated evidence points; NOT probability or win rate',
            'modeled_gamma':'NEGATIVE' if gex<0 else 'NONNEGATIVE',
            'iv_trend':'UNKNOWN' if iv_change_pct_points is None else ('RISING' if iv_change_pct_points>0 else 'FALLING' if iv_change_pct_points<0 else 'FLAT'),
            'evidence':evidence,
            'warning':'OI signs are a proxy. No actual dealer positions, Vanna hedge direction, Charm hedge direction, or true order flow inferred.'}
