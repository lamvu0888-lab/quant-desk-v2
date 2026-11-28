"""Read-only cash SPX quotes and candles from Schwab Market Data Production."""
from datetime import datetime, timedelta, timezone
import time

import requests

from market_session import ET, active_session, rth_bounds, session_state
from schwab_auth import SchwabTokens
from terminal_data import numeric, positive, price_bars, time_or_none
from tradytics_feed import Feed, FeedError, FeedStore, Snapshot

UTC = timezone.utc
BASE = "https://api.schwabapi.com/marketdata/v1"
SYMBOL = "$SPX"
MINUTE = timedelta(minutes=1)
QUOTE_AGE = 30


def quote_status(feed, now, paused=False):
    if paused:
        return "PAUSED"
    if feed.error:
        return feed.error.status
    if not feed.snapshot:
        return "LOADING"
    quote = feed.obj
    if quote.get("realtime") is not True:
        return "DELAYED" if quote.get("realtime") is False else "ENTITLEMENT UNKNOWN"
    at = time_or_none(quote.get("price_at"))
    if not at or at > now or not positive(quote.get("price")):
        return "QUOTE UNVERIFIED"
    session = active_session(now)
    opened, closed = rth_bounds(session)
    if at < opened:
        return "PRIOR SESSION"
    if at.astimezone(ET).date() != session:
        return "OUTSIDE SESSION"
    if opened <= now < closed:
        return "FRESH QUOTE" if (now - at).total_seconds() <= QUOTE_AGE else "AGED"
    return "SESSION CLOSE" if at >= closed - 2 * MINUTE else "AGED"


def normalize_quote(body, now):
    obj = body.get(SYMBOL)
    if (not isinstance(obj, dict) or obj.get("assetMainType") != "INDEX"
            or obj.get("symbol", SYMBOL) != SYMBOL):
        raise FeedError("SYMBOL UNAVAILABLE", "Schwab did not return the cash $SPX index.")
    raw = obj.get("quote")
    if not isinstance(raw, dict) or positive(raw.get("lastPrice")) is None:
        raise FeedError("NO QUOTE", "Schwab returned no cash index level.")
    # A recently received response or a newer quote update cannot refresh an old last price.
    stamp = time_or_none(raw["tradeTime"]) if positive(raw.get("tradeTime")) else None
    if stamp is None and positive(raw.get("quoteTime")):
        stamp = time_or_none(raw["quoteTime"])
    stamp = stamp.isoformat() if stamp else None
    price, previous = positive(raw["lastPrice"]), positive(raw.get("closePrice"))
    realtime = obj.get("realtime") if isinstance(obj.get("realtime"), bool) else None
    change = numeric(raw.get("netPercentChange"))
    quote = {"symbol": "SPX", "name": "S&P 500", "price": price, "price_at": stamp,
             "previous_close": previous, "realtime": realtime,
             "change_pct": change if change is not None else (100 * (price / previous - 1) if previous else None)}
    return Snapshot(quote, {"symbol": "SPX", "source": "Schwab"}, now)


def normalize_history(body, now, *, daily=False):
    identity = body.get("symbol")
    if (identity != SYMBOL and not (identity == "SPX" and body.get("isIndex") is True)) or body.get("isIndex") is False:
        raise FeedError("SYMBOL UNAVAILABLE", "Schwab history did not identify the requested cash $SPX series.")
    if body.get("realtime") is False or (numeric(body.get("delay_minutes")) or 0) > 0:
        raise FeedError("DELAYED", "Schwab marked the cash SPX history as delayed.")
    raw = body.get("candles")
    if not isinstance(raw, list) or body.get("empty") is True or not raw:
        raise FeedError("NO CANDLES", "Schwab returned no cash $SPX candles for this request.", 15)
    rows = [{**r, "symbol": "SPX", "time": r.get("datetime")} for r in raw
            if isinstance(r, dict) and r.get("symbol", SYMBOL) == SYMBOL]
    meta = {"symbol": "SPX", "source": "Schwab"}
    return price_bars(Feed(Snapshot(rows, meta, now)), "SPX", now, daily=daily)


class SchwabMarketData:
    configured = True  # constructed only with an app key and secret
    supports_futures_proxy = True

    def __init__(self, tokens):
        self.tokens = tokens
        from schwab_futures import SampledFutures
        self._futures = SampledFutures()
        self._history = []
        self._full_read = 0

    @staticmethod
    def refresh_delay(seconds):
        # FeedStore also serves Tradytics, whose client can apply extra quota throttling.
        return seconds

    def _get(self, endpoint, params):
        token = self.tokens.access_token()
        for attempt in range(2):
            try:
                response = requests.get(BASE + endpoint, params=params, timeout=(4, 8), allow_redirects=False,
                                        headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
            except requests.RequestException:
                raise FeedError("UNAVAILABLE", "Schwab market data could not be reached.") from None
            if response.status_code == 401 and attempt == 0:
                token = self.tokens.access_token(rejected=token)
                continue
            break
        if response.status_code == 401:
            raise FeedError("AUTH REQUIRED", "Schwab rejected the access token. Renew local authorization.", 60)
        if response.status_code == 403:
            raise FeedError("ACCESS DENIED", "Check Schwab Market Data Production access and index entitlements.", 60)
        if response.status_code == 429:
            wait = numeric(response.headers.get("Retry-After"))
            raise FeedError("COOLDOWN", "Schwab market data is rate limited.", max(5, wait or 60))
        if response.status_code != 200:
            raise FeedError("UNAVAILABLE", f"Schwab market data returned HTTP {response.status_code}.")
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise FeedError("READ ERROR", "Schwab returned an unrecognized market-data response.")
        return body

    def get(self, name, *, session=None, symbol=None, expiry=None, right=None):
        if name == "futures_proxy":
            raw = self._get("/quotes", {"symbols": "/ES,/MES", "fields": "quote,reference"})
            received = datetime.now(UTC)
            observed = self._futures.observe(raw, received)
            return Snapshot(observed, {"source": "Schwab", "delay_minutes": 0 if observed else None}, received)
        if name in ("option_expirations", "option_chain"):
            from contract_sources import read_schwab_contracts
            return read_schwab_contracts(self,name,symbol,expiry,right)
        if name == "quote":
            raw = self._get("/quotes", {"symbols": SYMBOL, "fields": "quote"})
            return normalize_quote(raw, datetime.now(UTC))
        if name not in {"history", "daily"}:
            raise ValueError("Unknown Schwab market-data feed")
        now = datetime.now(UTC)
        params = {"symbol": SYMBOL, "frequency": 1, "needExtendedHoursData": "false"}
        daily = name == "daily"
        rolling = not daily and session is None
        incremental = rolling and bool(self._history) and time.monotonic() - self._full_read < 1800
        if daily:
            params.update(periodType="year", period=1, frequencyType="daily")
        else:
            if session:
                bounds = rth_bounds(session)
                if not bounds or bounds[0] > now:
                    raise FeedError("NO SESSION", "No completed cash SPX session for that date.")
                start, end = bounds[0], min(now, bounds[1])
            else:
                end = now
                start = now - timedelta(days=8)
                if incremental:
                    start = max(start, time_or_none(self._history[-1]["time_utc"]) - 2 * MINUTE)
            params.update(periodType="day", frequencyType="minute",
                          startDate=int(start.timestamp() * 1000), endDate=int(end.timestamp() * 1000))
        raw = self._get("/pricehistory", params)
        received = datetime.now(UTC)
        rows = normalize_history(raw, received, daily=daily)
        if not daily:
            rows = [r for r in rows if start <= time_or_none(r["time_utc"]) < end]
        if rolling:
            if incremental:
                cutoff = (now - timedelta(days=8)).isoformat()
                merged = {r["time_utc"]: r for r in self._history if r["time_utc"] >= cutoff}
                merged.update((r["time_utc"], r) for r in rows)
                rows = [merged[t] for t in sorted(merged)]
            else:
                self._full_read = time.monotonic()
            self._history = rows
        return Snapshot({"candles": rows}, {"symbol": "SPX", "source": "Schwab", "session": session}, received)


def cadence(name, snapshot):
    if name == "futures_proxy":
        return 3 if session_state(snapshot.received_at)["status"] == "OPEN" else 60
    if name in ("daily", "option_expirations") or snapshot.meta.get("session"):
        return 300
    state = session_state(snapshot.received_at)
    opened = time_or_none(state["open_utc"])
    if state["status"] == "OPEN" or (opened and 0 <= (opened - snapshot.received_at).total_seconds() <= 60):
        return 5 if name == "quote" else 15
    return 60


def feed_store(config):
    tokens = SchwabTokens.from_config(config)
    return FeedStore(SchwabMarketData(tokens), cadence=cadence) if tokens else None
