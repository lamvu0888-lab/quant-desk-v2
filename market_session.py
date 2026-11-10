"""NYSE sessions, holidays and early closes. All API times stay in UTC."""
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pandas as pd
from data_utils import DataError, utc

ET = ZoneInfo('America/New_York')


@lru_cache(maxsize=1)
def calendar():
    import exchange_calendars as xc
    return xc.get_calendar('XNYS')


def session_state(now=None):
    now = utc(now or datetime.now(timezone.utc))
    day = now.astimezone(ET).date().isoformat()
    try:
        cal = calendar()
        if not cal.is_session(day):
            return {'status': 'CLOSED', 'minutes_left': 0, 'open_utc': None, 'close_utc': None}
        opened, closed = cal.session_open(day), cal.session_close(day)
        status = 'OPEN' if opened <= pd.Timestamp(now) < closed else 'PREMARKET' if now < opened else 'CLOSED'
        return {'status': status, 'minutes_left': max(0, int((closed.to_pydatetime() - now).total_seconds() // 60)) if status == 'OPEN' else 0,
                'open_utc': opened.isoformat(), 'close_utc': closed.isoformat()}
    except Exception as exc:
        raise DataError('Market calendar unavailable; live forecasts paused') from exc


def rth_bounds(day):
    try:
        c = calendar()
        if not c.is_session(str(day)):
            return None
        return c.session_open(str(day)).to_pydatetime(), c.session_close(str(day)).to_pydatetime()
    except Exception as exc:
        raise DataError('Market calendar cannot validate candle session') from exc


def active_session(now):
    """Today's session after it opens, otherwise the last completed session."""
    local_day = now.astimezone(ET).date()
    bounds = rth_bounds(local_day)
    if not bounds or now < bounds[0]:
        local_day -= timedelta(days=1)
    return calendar().date_to_session(local_day.isoformat(), direction="previous").date()
