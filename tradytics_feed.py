"""Read-only Tradytics REST client and a shared, bounded snapshot cache."""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import requests

from data_utils import DataError, utc
from market_session import ET

BASE_URL = "https://tradytics.com/api/public/v1"
UTC = timezone.utc


@dataclass(frozen=True)
class Endpoint:
    path: str
    label: str
    scope: str
    refresh_seconds: int


ENDPOINTS = {
    "quotes": Endpoint("/stocks/quotes", "Stock / ETF quote", "market", 30),
    "indices": Endpoint("/market/indices", "Index levels", "market", 30),
    "bars": Endpoint("/stocks/bars", "Price candles", "market", 30),
    "breadth": Endpoint("/market/breadth", "Market breadth", "market", 60),
    "timeline": Endpoint("/options/flow/timeline", "Net options flow", "options", 20),
    "flow": Endpoint("/options/flow", "Options tape", "options", 12),
    "exposure": Endpoint("/options/exposure", "Dealer positioning", "options", 45),
    "exposure_history": Endpoint("/options/exposure/history", "Daily exposure", "options", 600),
    "chain": Endpoint("/options/chain", "Option chain", "options", 35),
    "volatility": Endpoint("/options/volatility", "Implied volatility", "options", 90),
    "volatility_history": Endpoint("/options/volatility/history", "Daily volatility", "options", 600),
    "dark_prints": Endpoint("/darkpool/prints", "Dark-pool tape", "darkpool", 20),
    "dark_levels": Endpoint("/darkpool/levels", "Dark-pool levels", "darkpool", 120),
    "dark_daily": Endpoint("/darkpool/daily", "Dark-pool sessions", "darkpool", 300),
    "signals": Endpoint("/signals", "Tradytics signals", "signals", 35),
    "signal": Endpoint("/signals/{id}", "Signal detail", "signals", 60),
    "performance": Endpoint("/signals/performance", "Signal performance", "signals", 300),
}


class FeedError(DataError):
    def __init__(self, status, message, retry_seconds=30):
        self.status = status
        self.retry_seconds = retry_seconds
        super().__init__(message)


@dataclass(frozen=True)
class Snapshot:
    data: list | dict | None
    meta: dict
    received_at: datetime
    cursor: str | None = None


@dataclass(frozen=True)
class Feed:
    snapshot: Snapshot | None = None
    error: FeedError | None = None
    loading: bool = False

    @property
    def rows(self):
        if self.snapshot is None:
            return []
        data = self.snapshot.data
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            for name in ("rows", "items", "prints", "bars", "quotes", "levels", "signals", "results", "contracts", "indices"):
                if isinstance(data.get(name), list):
                    return [row for row in data[name] if isinstance(row, dict)]
        return []

    @property
    def obj(self):
        if self.snapshot and isinstance(self.snapshot.data, dict):
            return self.snapshot.data
        return {}


def _cooldown(headers, default):
    try:
        seconds = float(headers.get("Retry-After", default))
        if math.isfinite(seconds) and seconds >= 0:
            return max(1, seconds)
    except (ValueError, TypeError):
        pass
    return default


class TradyticsClient:
    def __init__(self, key, auth_mode="query", *, transport=requests.get):
        self._key = key.strip()
        if auth_mode not in ("query", "bearer"):
            raise FeedError("CONFIG", "Set TRADYTICS_AUTH_MODE to query or bearer in .env.", 300)
        self.auth_mode = auth_mode
        self._get = transport
        self._lock = threading.Lock()
        self._recent_calls = deque()
        self._blocked_until = 0.0
        self._blocked_error = None
        self._scope_errors = {}
        self._remaining = None

    @property
    def configured(self):
        return bool(self._key)

    def budget(self):
        now = time.monotonic()
        with self._lock:
            return {
                "calls_last_minute": sum(now - t < 60 for t in self._recent_calls),
                "local_limit": 150,
                "cooldown_seconds": max(0, round(self._blocked_until - now)),
                "vendor_minute_remaining": self._remaining,
            }

    def _reserve(self, scope):
        if not self.configured:
            raise FeedError("KEY REQUIRED", "Set TRADYTICS_API_KEY in the local .env file.", 300)
        now = time.monotonic()
        with self._lock:
            if now < self._blocked_until:
                error = self._blocked_error
                raise FeedError(error.status, str(error), self._blocked_until - now)
            denied_until = self._scope_errors.get(scope, 0)
            if now < denied_until:
                raise FeedError("SCOPE REQUIRED", f"Enable the {scope} scope on your Tradytics key.", denied_until - now)
            while self._recent_calls and now - self._recent_calls[0] >= 60:
                self._recent_calls.popleft()
            if len(self._recent_calls) >= 150:
                raise FeedError("COOLDOWN", "Local Tradytics request budget is cooling down.", 60 - (now - self._recent_calls[0]))
            self._recent_calls.append(now)

    def _block(self, error):
        with self._lock:
            deadline = time.monotonic() + error.retry_seconds
            if deadline >= self._blocked_until:
                self._blocked_until = deadline
                self._blocked_error = error
        return error

    def get(self, name, **params):
        endpoint = ENDPOINTS[name]
        path = endpoint.path
        if name == "signal":
            try:
                signal_id = int(params.pop("id"))
                if signal_id < 0:
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise FeedError("INPUT", "Choose a signal to inspect.") from None
            path = path.format(id=signal_id)
        self._reserve(endpoint.scope)
        query = {key: value for key, value in params.items() if value is not None and key != "apiKey"}
        headers = {"Accept": "application/json", "User-Agent": "QuantDesk/2.0"}
        if self.auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {self._key}"
        else:
            query["apiKey"] = self._key
        # Disallow redirects so a URL credential cannot follow another host.
        try:
            response = self._get(BASE_URL + path, params=query, headers=headers, timeout=(4, 9), allow_redirects=False)
        except requests.RequestException:
            raise FeedError("NETWORK", "Tradytics could not be reached. Retrying shortly.") from None
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        status = response.status_code
        error = payload.get("error") if isinstance(payload, dict) else None
        code = error.get("code") if isinstance(error, dict) else error
        code = code or (payload.get("code") if isinstance(payload, dict) else None)
        remaining = response.headers.get("X-RateLimit-Remaining")
        if isinstance(remaining, str) and remaining.isdecimal():
            with self._lock:
                self._remaining = int(remaining)
        if status == 429:
            default = 60
            if code == "quota_exceeded":
                now = datetime.now(UTC)
                tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                default = (tomorrow - now).total_seconds()
            raise self._block(FeedError("COOLDOWN", "Tradytics rate limit reached. Automatic retry is scheduled.", _cooldown(response.headers, default)))
        if status == 401:
            raise self._block(FeedError("CHECK KEY", "Tradytics rejected the key. Check it in .env and relaunch.", 300))
        if status == 403:
            if code == "missing_scope":
                with self._lock:
                    self._scope_errors[endpoint.scope] = time.monotonic() + 180
            raise FeedError("SCOPE REQUIRED", f"Check Pro access and the {endpoint.scope} scope for this endpoint.", 180)
        if status == 404:
            raise FeedError("UNAVAILABLE", "Tradytics has no result for this symbol, date or endpoint.", 180)
        if status != 200:
            raise FeedError("REQUEST ERROR", f"Tradytics returned HTTP {status} for {endpoint.label}.", 45)
        if not isinstance(payload, dict) or "data" not in payload or not isinstance(payload["data"], (dict, list, type(None))):
            raise FeedError("FORMAT ERROR", f"Unexpected Tradytics response for {endpoint.label}.")
        meta = dict(payload["meta"]) if isinstance(payload.get("meta"), dict) else {}
        for field in ("as_of", "date", "session", "symbol", "timeframe", "count"):
            if field in payload and field not in meta:
                meta[field] = payload[field]
        cursor = payload.get("next_cursor")
        return Snapshot(payload["data"], meta, datetime.now(UTC), str(cursor) if cursor is not None else None)


@dataclass
class _Entry:
    feed: Feed = Feed()
    next_due: float = 0
    head_at: float = 0
    market_day: str = ""


def _print_id(row):
    value = row.get("id")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _print_time(row):
    try:
        return utc(row.get("time")).timestamp()
    except DataError:
        return 0.0


class FeedStore:
    """One worker pool for all pages. Only requested feeds refresh."""

    def __init__(self, client):
        self.client = client
        self._pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="tradytics")
        self._lock = threading.RLock()
        self._entries = OrderedDict()
        self._pending = 0
        self._closed = False

    def close(self):
        with self._lock:
            self._closed = True
            self._pool.shutdown(wait=False, cancel_futures=True)

    def refresh(self):
        with self._lock:
            for entry in self._entries.values():
                entry.next_due = 0

    def get(self, name, *, paused=False, **params):
        if not self.client.configured:
            return Feed(error=FeedError("KEY REQUIRED", "Set TRADYTICS_API_KEY in .env."))
        params = {key: value for key, value in params.items() if value is not None}
        key = (name, tuple(sorted(params.items())))
        now = time.monotonic()
        with self._lock:
            entry = self._entries.setdefault(key, _Entry())
            self._entries.move_to_end(key)
            if len(self._entries) > 100:
                removable = next((k for k, e in self._entries.items() if not e.feed.loading and k != key), None)
                if removable is not None:
                    self._entries.pop(removable)
            if not (paused or self._closed or entry.feed.loading or now < entry.next_due or self._pending >= 12):
                entry.feed = replace(entry.feed, loading=True)
                self._pending += 1
                future = self._pool.submit(self._read, name, params, entry.feed.snapshot, entry.head_at, entry.market_day)
                future.add_done_callback(lambda result: self._finish(name, entry, result))
            return entry.feed

    def _read(self, name, params, previous, head_at, market_day):
        now = time.monotonic()
        day = datetime.now(ET).date().isoformat()
        # Rebase the tape every 75 seconds so final NBBO classifications and
        # cancellations replace provisional rows. Dates use a fresh snapshot.
        tape = name == "flow" and not params.get("date")
        old_rows = Feed(previous).rows if tape and previous and market_day == day else []
        oldest_first_cursor = max((i for row in old_rows if (i := _print_id(row)) is not None), default=None)
        if oldest_first_cursor is None or now - head_at >= 75:
            result = self.client.get(name, **params)
            return result, now, day
        result = self.client.get(name, **params, since=oldest_first_cursor)
        new_rows = list(Feed(result).rows)
        cursor = result.cursor
        seen = set()
        while cursor and cursor not in seen and len(seen) < 2:
            seen.add(cursor)
            page = self.client.get(name, **params, since=oldest_first_cursor, cursor=cursor)
            new_rows.extend(Feed(page).rows)
            cursor = page.cursor
        rows_by_id = {}
        for row in old_rows + new_rows:
            row_id = _print_id(row)
            row_key = row_id if row_id is not None else (row.get("time"), row.get("contract"), row.get("size"), row.get("price"))
            rows_by_id[row_key] = row
        merged = sorted(rows_by_id.values(), key=lambda row: (_print_time(row), _print_id(row) or 0), reverse=True)
        return replace(result, data=merged[:int(params.get("limit", 250))]), head_at, day

    def _finish(self, name, entry, future):
        with self._lock:
            self._pending -= 1
            if future.cancelled():
                entry.feed = replace(entry.feed, loading=False)
                return
            try:
                snapshot, head_at, day = future.result()
            except FeedError as exc:
                entry.feed = Feed(entry.feed.snapshot, exc)
                entry.next_due = time.monotonic() + max(1, exc.retry_seconds)
            except Exception:
                entry.feed = Feed(entry.feed.snapshot, FeedError("READ ERROR", "This feed could not be decoded. Retrying shortly."))
                entry.next_due = time.monotonic() + 30
            else:
                entry.feed = Feed(snapshot)
                entry.head_at, entry.market_day = head_at, day
                entry.next_due = time.monotonic() + ENDPOINTS[name].refresh_seconds
