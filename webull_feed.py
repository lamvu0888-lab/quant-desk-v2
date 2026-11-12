"""Background Webull reads for the dashboard and alert worker."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from data_utils import BudgetPause, DataError, reported_delay, safe_error
from live_chain import contract_symbol, occ_info, unwrap
from market_session import ET, rth_bounds, session_state
from my_webull_adapter import WebullSDKAdapter, category_for, normalize_candles, normalize_quote
from rate_control import RequestBudget
from terminal_data import INDICES, numeric, time_or_none, valid_date
from tradytics_feed import Feed, FeedError, Snapshot

UTC = timezone.utc
OPTION_REFRESH_SECONDS = 15
BAR_RETRY_SECONDS = 5
MINUTE = timedelta(minutes=1)
INDEX_SYMBOLS = INDICES | {"SPXW"}


def price_source(symbol, preference, webull):
    if symbol not in INDEX_SYMBOLS and (preference == "Webull" or preference == "Auto" and webull):
        return "Webull"
    return "Tradytics"


def supports_options(symbol):
    return symbol not in INDEX_SYMBOLS or symbol == "SPX"


@dataclass
class _Entry:
    feed: Feed = Feed()
    due: float = 0.0


def _symbol_in_payload(payload, symbol):
    if isinstance(payload, dict):
        return (payload.get("symbol") == symbol or symbol in payload or
                any(_symbol_in_payload(payload.get(key), symbol) for key in ("data", "result", "rows", "bars")))
    if isinstance(payload, list):
        return any(_symbol_in_payload(row, symbol) for row in payload)
    return False


def _error(exc):
    if isinstance(exc, BudgetPause):
        return FeedError("COOLDOWN", str(exc), exc.seconds)
    return FeedError("WEBULL UNAVAILABLE", safe_error(exc), 20)


class WebullFeedStore:
    def __init__(self, settings, app_key=None, app_secret=None, region=None, *, adapter_factory=None):
        self.settings = settings
        self._history_count = max(610, settings.history_count)
        self.budget = RequestBudget(settings.max_requests)
        self._adapter_factory = adapter_factory or (lambda: WebullSDKAdapter(
            settings, app_key=app_key, app_secret=app_secret, region=region))
        self._adapter = None
        self._adapter_error = None
        self._adapter_retry = 0.0
        self._adapter_lock = threading.Lock()
        self._lock = threading.RLock()
        self._closed = False
        self._entries = {kind: OrderedDict() for kind in ("quote", "history", "options")}
        self._category_hints = OrderedDict()
        self._pending = {kind: 0 for kind in self._entries}
        self._pools = {kind: ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"webull-{kind}")
                       for kind in self._entries}
        # Only the option worker accesses the discovery cache.
        self._contracts = OrderedDict()

    def _client(self):
        with self._adapter_lock:
            if self._adapter is None:
                if time.monotonic() < self._adapter_retry:
                    raise DataError(self._adapter_error)
                try:
                    self._adapter = self._adapter_factory()
                except Exception as exc:
                    self._adapter_error = safe_error(exc)
                    self._adapter_retry = time.monotonic() + 30
                    raise DataError(self._adapter_error) from None
            return self._adapter

    def close(self):
        with self._lock:
            self._closed = True
            for pool in self._pools.values():
                pool.shutdown(wait=False, cancel_futures=True)

    def refresh(self):
        with self._lock:
            for entries in self._entries.values():
                for entry in entries.values():
                    entry.due = 0

    def _entry(self, kind, key):
        entries = self._entries[kind]
        entry = entries.setdefault(key, _Entry())
        entries.move_to_end(key)
        if len(entries) > 48:
            old = next((k for k, e in entries.items() if k != key and not e.feed.loading), None)
            if old is not None:
                entries.pop(old)
        return entry

    def _schedule(self, kind, entries, read, *args):
        for entry in entries.values():
            entry.feed = replace(entry.feed, loading=True)
        self._pending[kind] += 1
        future = self._pools[kind].submit(read, *args)
        future.add_done_callback(lambda result: self._finish(kind, entries, result))

    def _finish(self, kind, entries, future):
        with self._lock:
            self._pending[kind] -= 1
            try:
                snapshots = future.result()
            except Exception as exc:
                snapshots = dict.fromkeys(entries, _error(exc))
            for key, entry in entries.items():
                snapshot = snapshots[key]
                if isinstance(snapshot, FeedError):
                    if snapshot.status == "COOLDOWN" and entry.feed.snapshot:
                        entry.feed = replace(entry.feed, loading=False)
                    else:
                        entry.feed = Feed(entry.feed.snapshot, snapshot)
                    delay = snapshot.retry_seconds
                else:
                    entry.feed = Feed(snapshot)
                    delay = self._refresh_delay(kind, snapshot)
                entry.due = time.monotonic() + delay

    def _refresh_delay(self, kind, snapshot):
        now = snapshot.received_at
        opened = session_state(now)["status"] == "OPEN"
        if kind == "options":
            return OPTION_REFRESH_SECONDS if opened else 60
        if kind == "quote":
            return self.settings.quote_seconds if opened else 30
        if not opened:
            return self.settings.candle_seconds
        last = time_or_none(snapshot.data.get("last_bar_utc"))
        next_close = last + 2 * MINUTE if last else now
        return max(BAR_RETRY_SECONDS, (next_close - now).total_seconds() + 1.5)

    def prices(self, symbols, *, paused=False):
        """Return cached observations immediately and batch due symbols by category."""
        now = time.monotonic()
        result = defaultdict(dict)
        with self._lock:
            groups = defaultdict(list)
            for symbol in dict.fromkeys(symbols):
                if symbol not in INDEX_SYMBOLS:
                    category = self._category_hints.get(symbol) or category_for(symbol, self.settings.category)
                    groups[category].append(symbol)
            for kind in ("quote", "history"):
                for category, tickers in groups.items():
                    entries = {ticker: self._entry(kind, (ticker, category)) for ticker in tickers}
                    due = {ticker: entry for ticker, entry in entries.items()
                           if not entry.feed.loading and now >= entry.due}
                    if due and not (self._closed or paused) and self._pending[kind] < 2:
                        previous = {ticker: entry.feed.obj for ticker, entry in due.items()}
                        self._schedule(kind, due, self._read_prices, kind, category, previous)
                    for ticker, entry in entries.items():
                        result[ticker][kind] = entry.feed
        return result

    def _read_prices(self, kind, category, previous):
        client = self._client()
        started = datetime.now(UTC)
        tickers = tuple(previous)
        if kind == "quote":
            raw = client.quote_batch(tickers, category, self.budget)
        else:
            # Keep a warm history and request a short overlapping tail on normal updates.
            recent = all(data and started - time_or_none(data["last_bar_utc"]) < 7 * MINUTE
                         for data in previous.values())
            raw = client.history_batch(tickers, category, 10 if recent else self._history_count, self.budget)
        received = datetime.now(UTC)
        result = {}
        for ticker in tickers:
            try:
                if kind == "quote":
                    if self.settings.category == "AUTO" and not _symbol_in_payload(raw, ticker):
                        with self._lock:
                            if ticker not in self._category_hints:
                                self._category_hints[ticker] = "US_ETF" if category == "US_STOCK" else "US_STOCK"
                                if len(self._category_hints) > 48:
                                    self._category_hints.popitem(last=False)
                    data = normalize_quote(raw, ticker, now=received)
                else:
                    if len(tickers) > 1 and not _symbol_in_payload(raw, ticker):
                        raise DataError("Webull returned no bars identified with this ticker")
                    data = normalize_candles(raw, ticker, now=started)
                    if previous[ticker] and data["last_bar_utc"] < previous[ticker]["last_bar_utc"]:
                        raise DataError("Webull candle response is older than the previous update")
                    merged = {bar["time_utc"]: bar for bar in previous[ticker].get("candles", [])}
                    merged.update({bar["time_utc"]: bar for bar in data["candles"]})
                    data["candles"] = [merged[stamp] for stamp in sorted(merged)[-self._history_count:]]
                    # Keep the actual latest market time in this response for freshness checks.
                    data["received_utc"] = received.isoformat()
                result[ticker] = Snapshot(data, {"symbol": ticker, "delay_minutes": data["delay_minutes"],
                                                 "sandbox": self.settings.endpoint == "api.sandbox.webull.com"}, received)
            except DataError as exc:
                result[ticker] = _error(exc)
        return result

    def options(self, symbol, expiry, spot, now, *, paused=False):
        day = valid_date(expiry)
        bounds = rth_bounds(day) if day else None
        if not supports_options(symbol) or not bounds or spot is None or now >= bounds[1]:
            return Feed()
        key = (symbol, expiry)
        with self._lock:
            entry = self._entry("options", key)
            if (not (paused or self._closed or entry.feed.loading) and self._pending["options"] < 2
                    and time.monotonic() >= entry.due):
                self._schedule("options", {key: entry}, self._read_options, key, spot, bounds[1])
            return entry.feed

    def _option_request(self, label, method, **params):
        # Honor the shared pacing without making a UI or price request wait for an option page.
        for attempt in range(3):
            try:
                return self._client().request(label, method, self.budget,
                                              budget_reserve=max(4, self.settings.max_requests // 4), **params)
            except BudgetPause as exc:
                if exc.seconds > 1.1 or attempt == 2:
                    raise
                time.sleep(exc.seconds)

    def _discover(self, key, root, spot):
        symbol, expiry = key
        saved = self._contracts.get(key)
        width = self.settings.strike_width
        if (saved and time.monotonic() - saved[0] < self.settings.discovery_seconds
                and abs(spot / saved[1] - 1) < width / 3):
            return saved[2]
        low, high = round(spot * (1 - width), 3), round(spot * (1 + width), 3)
        contracts, seen, cursor = {}, set(), None
        method = self._client().data.instrument.list_option_contracts
        for _ in range(min(4, self.settings.max_pages)):
            raw = self._option_request("Option discovery", method, category="US_OPTION", underlying_symbols=symbol,
                                       status="LISTING", start_date=expiry, root_symbol=root,
                                       strike_price_gte=str(low), strike_price_lte=str(high), pagination_key=cursor)
            rows, cursor = unwrap(raw)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                code = str(contract_symbol(row) or "").replace(" ", "").upper()
                try:
                    series, expires, right, strike = occ_info(code)
                except DataError:
                    continue
                if (series != root or expires.isoformat() != expiry or not low <= strike <= high
                        or row.get("underlying_symbol") not in (None, symbol, root)
                        or row.get("def_type") not in (None, "STANDARD") or numeric(row.get("multiplier")) != 100):
                    continue
                contracts[code] = {"contract": code, "symbol": symbol, "root": root, "expiry": expiry,
                                   "type": "call" if right == "C" else "put", "strike": strike,
                                   "multiplier": 100, "standard": True}
            if not cursor:
                break
            if cursor in seen or not rows:
                raise DataError("Webull option discovery returned an invalid cursor")
            seen.add(cursor)
        else:
            raise DataError("Webull option discovery exceeded the near-spot page limit")
        if not contracts:
            raise DataError(f"Webull returned no standard {root} contracts for {expiry}")
        self._contracts[key] = (time.monotonic(), spot, contracts)
        self._contracts.move_to_end(key)
        if len(self._contracts) > 48:
            self._contracts.popitem(last=False)
        return contracts

    def _read_options(self, key, spot, expires_at):
        symbol, expiry = key
        root = "SPXW" if symbol == "SPX" else symbol
        contracts = self._discover(key, root, spot)
        # Pair the same strikes when possible for an auditable ATM IV estimate.
        ranked = sorted(contracts.values(), key=lambda row: (abs(row["strike"] - spot), row["strike"]))
        selected = [row["contract"] for right in ("call", "put") for row in
                    [row for row in ranked if row["type"] == right][:10]]
        raw = self._option_request("Option snapshots", self._client().data.option_market_data.get_option_snapshot,
                                   category="US_OPTION", symbols=",".join(selected))
        rows, _ = unwrap(raw)
        received = datetime.now(UTC)
        parsed = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(contract_symbol(row) or "").replace(" ", "").upper()
            if code not in selected:
                continue
            if code in parsed:
                raise DataError("Webull returned a duplicate option snapshot")
            stamp = time_or_none(row.get("quote_time"))
            parsed[code] = {**contracts[code], "expiry_utc": expires_at.isoformat(),
                            "bid": numeric(row.get("bid")), "ask": numeric(row.get("ask")),
                            "iv": numeric(row.get("imp_vol")), "volume": numeric(row.get("volume")),
                            "open_interest": numeric(row.get("open_interest")),
                            **{field: numeric(row.get(field)) for field in ("delta", "gamma", "vega", "theta")},
                            "updated_at": stamp.isoformat() if stamp else None,
                            "delay_minutes": reported_delay(row, code)}
        meta = {"symbol": symbol, "root": root, "expiry": expiry, "discovered": len(contracts),
                "requested": len(selected), "source": "Webull", "delay_minutes": reported_delay(raw),
                "sandbox": self.settings.endpoint == "api.sandbox.webull.com"}
        return {key: Snapshot(list(parsed.values()), meta, received)}


def webull_price_quality(quote, bars, settings, now, *, paused=False):
    """Use observed market times. Missing delay metadata is reported separately."""
    q, h = quote.obj, bars.obj
    day = now.astimezone(ET).date()
    stamp = time_or_none(q.get("price_time_utc"))
    bid_ask = time_or_none(q.get("quote_time_utc"))
    price, kind = q.get("last_price"), "Last trade"
    if ((stamp is None or now - stamp > timedelta(seconds=settings.quote_max_age))
            and bid_ask and q.get("midpoint") and 0 <= (now - bid_ask).total_seconds() <= settings.quote_max_age):
        stamp, price, kind = bid_ask, q["midpoint"], "Bid/ask midpoint"
    last = time_or_none(h.get("last_bar_utc"))
    bar_close = last + MINUTE if last else None
    price_age = (now - stamp).total_seconds() if stamp else None
    bar_age = (now - bar_close).total_seconds() if bar_close else None
    allowed = not paused and settings.endpoint == "api.webull.com"
    quote_ok = bool(allowed and not quote.error and price and stamp and stamp.astimezone(ET).date() == day
                    and 0 <= price_age <= settings.quote_max_age and q.get("delay_minutes") in (None, 0))
    bars_ok = bool(allowed and not bars.error and bar_close and bar_close.astimezone(ET).date() == day
                   and 0 <= bar_age <= 120 and h.get("delay_minutes") in (None, 0))
    reasons = []
    if not allowed:
        reasons.append("Webull polling is paused." if paused else "Webull sandbox data is for testing.")
    if not quote_ok:
        reasons.append(str(quote.error) if quote.error else "Waiting for a fresh Webull price timestamp.")
    if not bars_ok:
        reasons.append(str(bars.error) if bars.error else "Waiting for a recent completed Webull 1-minute candle.")
    if len(h.get("candles", [])) < 121:
        reasons.append("At least 121 completed one-minute candles are needed for forecasts.")
    regular = session_state(now)["status"] == "OPEN"
    if not regular:
        reasons.append("Waiting for the regular session.")
    return {"quote_ready": quote_ok and regular, "bars_ready": bars_ok, "forecast_ready": not reasons,
            "price": price, "price_kind": kind, "price_time_utc": stamp.isoformat() if stamp else None,
            "price_age_seconds": price_age, "candle_age_seconds": bar_age, "delay_minutes": q.get("delay_minutes"),
            "status": "Fresh" if quote_ok and regular else "Waiting for quote", "reasons": reasons}
