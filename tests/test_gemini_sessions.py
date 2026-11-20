from datetime import datetime, timedelta, timezone
from gemini_sessions import FIVE, summarize, revision_reason

def test_partial_opening_waits_for_completed_bars():
    start = datetime(2026,11,19,14,30,tzinfo=timezone.utc)
    rows = [dict(time_utc=start+i*FIVE,open=10,high=11,low=9,close=10) for i in range(3)]
    assert summarize(rows,start,start+3*FIVE-timedelta(seconds=1),FIVE)["bars"] == 2
    assert summarize(rows,start,start+3*FIVE,FIVE)["bars"] == 3

def test_revision_requires_two_distinct_completed_bars_after_publication():
    start = datetime(2026,11,19,15,tzinfo=timezone.utc)
    daily = dict(state="ready",at=start,input={"spot":101,"market":"TREND UP"},
                 legs={"A":{"setup":"Breakout close","trigger":100,"invalidation":99}})
    bars = [dict(time_utc=start+i*FIVE,close=98) for i in range(2)]
    assert not revision_reason(daily,bars,"SIDEWAYS",start+FIVE,"SIDEWAYS")
    assert "Market changed" in revision_reason(daily,bars,"SIDEWAYS",start+2*FIVE,"SIDEWAYS")
    assert "invalidation" in revision_reason(daily,bars,"TREND UP",start+2*FIVE,"TREND UP")
