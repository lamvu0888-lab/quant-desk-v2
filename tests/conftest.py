import math
from datetime import datetime,timedelta,timezone
import numpy as np
import pytest
from market_session import rth_bounds


@pytest.fixture
def candles():
    rng=np.random.default_rng(447);rows=[];price=600.
    for day in ('2026-10-30','2026-11-02','2026-11-03'):
        start,close=rth_bounds(day)
        for i in range(390):
            o=price;price*=math.exp(float(rng.normal(0,.0007)))
            rows.append({'time_utc':(start+timedelta(minutes=i)).isoformat(),'open':o,
                         'high':max(o,price)*1.0001,'low':min(o,price)*.9999,'close':price,
                         'volume':10000.,'closed':True})
    return rows
