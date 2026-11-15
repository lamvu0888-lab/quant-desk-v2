"""Read-only ES/MES activity for SPX; futures prices never replace index prices."""
from datetime import timedelta
from data_utils import DataError, number, records, reported_delay, utc
from market_session import rth_bounds


def choose_contract(rows, root, day):
    choices = [r for r in rows if isinstance(r, dict) and r.get('code') == root
               and str(r.get('symbol', '')).startswith(root) and r.get('status') == 'OC'
               and str(r.get('last_trading_date', '')) >= str(day)]
    if not choices:
        raise DataError(f'No active {root} contract returned by Webull')
    return min(choices, key=lambda r: (str(r['last_trading_date']), str(r['symbol'])))['symbol']


def activity(bars_payload, footprint_payload, contract, now, day):
    bounds = rth_bounds(day)
    if not bounds or reported_delay(bars_payload, contract) != 0:
        raise DataError('Futures bars require explicit zero-delay data and a regular session')
    bars = {}
    for row in records(bars_payload, contract):
        stamp = utc(row.get('time'))
        if stamp.second or stamp.microsecond or not bounds[0] <= stamp < bounds[1] or stamp + timedelta(minutes=1) > now:
            continue
        bars[stamp] = number(row.get('volume'), 'futures contracts', minimum=0)
    delta = {}
    if footprint_payload is not None and reported_delay(footprint_payload, contract) == 0:
        for row in records(footprint_payload, contract):
            stamp = utc(row.get('time'))
            if stamp not in bars or row.get('buy_total') is None or row.get('sell_total') is None:
                continue
            buy, sell = number(row['buy_total'], 'buy contracts', minimum=0), number(row['sell_total'], 'sell contracts', minimum=0)
            if buy + sell <= bars[stamp] * 1.1:
                delta[stamp] = buy - sell
    points, cumulative, previous = [], 0, None
    for stamp, volume in sorted(bars.items()):
        if stamp not in delta or (previous and stamp - previous != timedelta(minutes=1)):
            cumulative = 0
        if stamp in delta:
            cumulative += delta[stamp]
        points.append({'time': (stamp + timedelta(minutes=1)).isoformat(), 'volume': volume,
                       'cvd': cumulative if stamp in delta else None})
        previous = stamp
    return points
