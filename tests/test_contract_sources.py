from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from contract_sources import contract_chain, contract_expirations
from option_quotes import quote_status
from schwab_feed import SchwabMarketData
from tradytics_feed import Feed, FeedError, Snapshot

NOW = datetime(2026, 11, 27, 16, tzinfo=timezone.utc)
EXP = "2026-11-27"


def schwab_body():
    option = dict(symbol="SPXW  261127C07000000",putCall="CALL",expirationDate=EXP+"T21:00:00Z",
                  multiplier=100,nonStandard=False,strikePrice=7000,bid=12.1,ask=12.25,volatility=19.4,
                  quoteTimeInLong=int(NOW.timestamp()*1000),delta=.4,gamma=.013,openInterest=12,totalVolume=57)
    return {"status":"SUCCESS","symbol":"$SPX","isDelayed":False,"interestRate":3.75,
            "callExpDateMap":{EXP+":0":{"7000.0":[option]}}}


def chain_feed(overrides=None):
    client=SchwabMarketData(None)
    client._get=Mock(return_value={**schwab_body(),**(overrides or {})})
    feed=Feed(client.get("option_chain",symbol="SPX",expiry=EXP,right="call"))
    client._get.assert_called_once_with("/chains",{"symbol":"$SPX","contractType":"CALL","strategy":"SINGLE",
                                       "fromDate":EXP,"toDate":EXP,"includeUnderlyingQuote":"true"})
    return feed


def test_request_normalizes_contract_identity_rate_iv_and_market_clock():
    f=chain_feed()
    assert f.rows[0]["contract"]=="SPXW261127C07000000"
    assert f.rows[0]["iv"]==pytest.approx(.194) and f.snapshot.meta["risk_free_rate"]==.0375
    assert quote_status(f.rows[0],f,NOW)=="Fresh quote"
    assert quote_status(f.rows[0],f,NOW+timedelta(minutes=3))=="Stale quote"
    for changes in (dict(nonStandard=True),dict(symbol="SPY   261127C07000000"),dict(expirationDate="2026-11-30")):
        body=schwab_body()
        body["callExpDateMap"][EXP+":0"]["7000.0"][0].update(changes)
        assert not chain_feed(body).rows


@pytest.mark.parametrize("changes",[{"isDelayed":True},{"isDelayed":None},{"status":"FAILED"},{"symbol":"SPY"},{"callExpDateMap":"invalid"}])
def test_schwab_rejects_wrong_underlying_or_unverified_chain(changes):
    with pytest.raises(FeedError):
        chain_feed(changes)


def test_contract_routing_does_not_change_quote_source_or_use_live_broker_in_preview():
    good=chain_feed()
    ctx=SimpleNamespace(ticker="SPX",preview=False,now=NOW,paused=False,option_max_age=120,
                        has_webull_options=True,schwab_store=Mock(),option_chain=Mock(return_value=(Feed(),"Webull")))
    ctx.schwab_store.get.return_value=good
    assert contract_chain(ctx,EXP,"call")== (good,"Schwab")
    ctx.option_chain.return_value=(good,"Webull")
    ctx.schwab_store.reset_mock()
    assert contract_chain(ctx,EXP,"call")== (good,"Webull")
    ctx.schwab_store.get.assert_not_called()
    ctx.preview=True
    ctx.option_chain.return_value=(good,"Preview")
    assert contract_chain(ctx,EXP,"call")[1]=="Preview"
    ctx.schwab_store.get.assert_not_called()


def test_actual_listed_dates_only_and_listing_fallback():
    client=SchwabMarketData(None)
    client._get=Mock(return_value={"expirationList":[{"expirationDate":EXP},{"expirationDate":"bad"},{"expirationDate":"2026-12-04"}]})
    listing=Feed(client.get("option_expirations",symbol="SPY"))
    assert [r["expiry"] for r in listing.rows]==[EXP,"2026-12-04"]
    client._get.assert_called_once_with("/expirationchain",{"symbol":"SPY"})
    ctx=SimpleNamespace(ticker="SPY",preview=False,session=NOW.date(),now=NOW,paused=False,get=Mock(return_value=Feed()),schwab_store=Mock())
    ctx.schwab_store.get.return_value=listing
    assert contract_expirations(ctx) is listing


def test_wrong_contract_or_synthetic_primary_cannot_mask_valid_secondary():
    good = chain_feed()
    ctx = SimpleNamespace(ticker="SPX", preview=False, now=NOW, paused=False, option_max_age=120,
                          has_webull_options=True, schwab_store=Mock(), option_chain=Mock())
    ctx.schwab_store.get.return_value = good
    for changes, metadata in (({"contract": "QQQ261127C07000000"}, {}),
                              ({}, {"source": "Preview"}), ({}, {"synthetic": True})):
        feed = Feed(Snapshot([{**good.rows[0], **changes}], {**good.snapshot.meta, **metadata}, NOW))
        ctx.option_chain.return_value = (feed, "Webull")
        assert contract_chain(ctx, EXP, "call") == (good, "Schwab")
    ctx.schwab_store.get.return_value = Feed()
    assert not contract_chain(ctx, EXP, "call")[0].rows
