"""Candle-based volatility regime analysis; independent of options OI model."""
from __future__ import annotations
from datetime import datetime, timezone
import math
import numpy as np

class CandleError(ValueError):
    pass

def analyze_volatility(candles, *, annual_bars=19656, lookback=100, bb_period=20, atr_period=14, confirm_bars=3):
    """Requires >= 120 closed, chronological 5-minute OHLCV candles.
    The 1-minute confirmation is optional and evaluated by the caller separately.
    No signal is emitted on insufficient or malformed data.
    """
    if not isinstance(candles, list) or len(candles) < max(120,lookback+bb_period):
        raise CandleError('Need at least 120 closed 5-minute candles (and lookback + BB period)')
    parsed=[]
    for bar in candles:
        try:
            ts=datetime.fromisoformat(str(bar['time_utc']).replace('Z','+00:00'))
            if ts.tzinfo is None: raise ValueError('timezone required')
            ts=ts.astimezone(timezone.utc)
            o,h,l,c,v=[float(bar[k]) for k in ('open','high','low','close','volume')]
            if not all(map(math.isfinite,(o,h,l,c,v))) or min(o,h,l,c)<=0 or v<0 or h<max(o,c,l) or l>min(o,c,h):
                raise ValueError('invalid OHLCV')
            if parsed and ts<=parsed[-1][0]: raise ValueError('timestamps must increase, no duplicates')
            parsed.append((ts,o,h,l,c,v))
        except (KeyError,TypeError,ValueError) as e:
            raise CandleError(f'Invalid candle: {e}') from e
    close=np.array([x[4] for x in parsed]); high=np.array([x[2] for x in parsed]); low=np.array([x[3] for x in parsed]); vol=np.array([x[5] for x in parsed])
    widths=[]
    for i in range(bb_period-1,len(close)):
        w=close[i-bb_period+1:i+1]
        widths.append(4*np.std(w,ddof=0)/np.mean(w))
    widths=np.array(widths)
    tr=np.maximum(high-low,np.maximum(np.abs(high-np.r_[close[0],close[:-1]]),np.abs(low-np.r_[close[0],close[:-1]])))
    atr=np.array([np.mean(tr[i-atr_period+1:i+1]) for i in range(atr_period-1,len(tr))])
    atr_ratio=float(atr[-1]/np.mean(atr[-min(lookback,len(atr)):])) if np.mean(atr[-min(lookback,len(atr)):])>0 else None
    bb_pct=float(100*np.mean(widths[-lookback:]<=widths[-1]))
    rising_bb=bool(np.all(np.diff(widths[-(confirm_bars+1):])>0))
    falling_bb=bool(np.all(np.diff(widths[-(confirm_bars+1):])<0))
    returns=np.diff(np.log(close))
    rv=float(np.std(returns[-20:],ddof=1)*np.sqrt(annual_bars)*100)
    rv_prev=float(np.std(returns[-40:-20],ddof=1)*np.sqrt(annual_bars)*100)
    vol_avg=float(np.mean(vol[-21:-1])); vol_ratio=float(vol[-1]/vol_avg) if vol_avg>0 else None
    compression=bb_pct<=20 and atr_ratio is not None and atr_ratio<.85
    expansion=rising_bb and atr_ratio is not None and atr_ratio>1.15 and rv>rv_prev
    prior_compressed=bool(100*np.mean(widths[-lookback-1:-1]<=widths[-2])<=20) if len(widths)>=lookback+1 else False
    breakout_up=bool(close[-1]>np.max(high[-21:-1]))
    breakout_down=bool(close[-1]<np.min(low[-21:-1]))
    breakout=breakout_up or breakout_down
    breakout_direction='UP' if breakout_up else 'DOWN' if breakout_down else 'NONE'
    price_direction='UP' if close[-1]>close[-4] else 'DOWN' if close[-1]<close[-4] else 'UNKNOWN'
    breakout_watch=prior_compressed and breakout and vol_ratio is not None and vol_ratio>=1.5 and rising_bb
    status='BREAKOUT WATCH' if breakout_watch else 'EXPANSION' if expansion else 'COMPRESSION' if compression else 'NEUTRAL'
    return {'status':status,'bb_width_pct':float(widths[-1]*100),'bb_width_percentile':bb_pct,
            'atr_ratio':atr_ratio,'atr':float(atr[-1]),'realized_vol_annual_pct':rv,
            'realized_vol_previous_pct':rv_prev,'volume_ratio':vol_ratio,
            'bb_width_rising':rising_bb,'bb_width_falling':falling_bb,
            'breakout_20bar':breakout,'breakout_direction':breakout_direction,'price_direction':price_direction,'closed_candles':len(close),
            'last_closed_candle_utc':parsed[-1][0].isoformat(),
            'note':'5m realized volatility regime; independent from option implied volatility'}

def demo_candles(ticker, count=180):
    """Deterministic synthetic 5m OHLCV; never presented as market observations."""
    from datetime import timedelta
    rng=np.random.default_rng(sum(ord(c) for c in ticker))
    start=datetime.now(timezone.utc).replace(second=0,microsecond=0)-timedelta(minutes=5*(count+1))
    price=100.;bars=[]
    for i in range(count):
        change=float(rng.normal(0,.20 if i<count-30 else .055))
        o=price;c=max(.01,o+change);h=max(o,c)+.04;l=min(o,c)-.04
        bars.append({'time_utc':(start+timedelta(minutes=5*i)).isoformat(), 'open':o,'high':h,'low':l,'close':c,'volume':1000+int(rng.integers(0,1000))})
        price=c
    return bars
