"""Read-only contract workspace routing. Worker execution uses its own established route."""
from datetime import datetime, timezone

from option_quotes import fresh_option_rows
from terminal_data import first_listed_dte, numeric, positive, time_or_none, valid_date
from tradytics_feed import Feed, FeedError, Snapshot
from v3_options import contract_spec
from v3_provenance import simulated_feed

SYMBOLS = {"SPX": "$SPX", "SPY": "SPY", "QQQ": "QQQ"}


def _schwab_row(raw, symbol, expiry, right):
    if not isinstance(raw, dict):
        return None
    if (raw.get("putCall") != right.upper() or raw.get("nonStandard") or raw.get("isMini")
            or raw.get("mini") or str(raw.get("expirationDate", ""))[:10] != expiry):
        return None
    timestamp = positive(raw.get("quoteTimeInLong"))
    at = time_or_none(timestamp) if timestamp else None
    volatility_pct = positive(raw.get("volatility"))
    row = {
        "symbol": symbol, "contract": str(raw.get("symbol", "")).replace(" ", ""),
        "expiry": expiry, "type": right, "strike": positive(raw.get("strikePrice")),
        "multiplier": numeric(raw.get("multiplier")), "updated_at": at.isoformat() if at else None,
        "bid_size": numeric(raw.get("bidSize")), "ask_size": numeric(raw.get("askSize")),
        "bid": positive(raw.get("bid")), "ask": positive(raw.get("ask")), "last": positive(raw.get("last")),
        "volume": numeric(raw.get("totalVolume")), "open_interest": numeric(raw.get("openInterest")),
        "iv": volatility_pct / 100 if volatility_pct is not None and volatility_pct <= 500 else None,
    }
    for greek in ("delta", "gamma", "theta", "vega", "rho"):
        val = numeric(raw.get(greek))
        row[greek] = val if val is not None and val != -999 and (greek != "delta" or abs(val) <= 1) else None
    try:
        spec = contract_spec(row, symbol)
    except (TypeError, ValueError):
        return None
    row["expiry_utc"] = min(spec.last_trade, spec.expires).isoformat()
    return row


def read_schwab_contracts(client, name, symbol, expiry=None, right=None):
    if symbol not in SYMBOLS:
        raise FeedError("UNSUPPORTED", "This contract workspace covers SPX, SPY and QQQ.")
    underlying = SYMBOLS[symbol]
    if name == "option_expirations":
        body = client._get("/expirationchain", {"symbol": underlying})
        items = body.get("expirationList", [])
        if body.get("symbol", underlying) != underlying or not isinstance(items, list):
            raise FeedError("SYMBOL UNVERIFIED", "Schwab listing identity could not be verified.")
        listed = {valid_date(str(row.get("expirationDate", ""))[:10]) for row in items if isinstance(row, dict)}
        dates = sorted(day for day in listed if day is not None)
        return Snapshot([{"symbol": symbol, "expiry": day.isoformat()} for day in dates],
                        {"source": "Schwab", "symbol": symbol}, datetime.now(timezone.utc))

    if name != "option_chain" or not valid_date(expiry) or right not in ("call", "put"):
        raise FeedError("INVALID SELECTION", "Choose a listed expiration and option side.")
    body = client._get("/chains", {"symbol": underlying, "contractType": right.upper(), "strategy": "SINGLE",
                                  "fromDate": expiry, "toDate": expiry, "includeUnderlyingQuote": "true"})
    if body.get("status") != "SUCCESS" or body.get("symbol") != underlying:
        raise FeedError("CHAIN UNAVAILABLE", "Schwab returned no verified chain for this underlying.")
    if body.get("isDelayed") is not False:
        raise FeedError("DELAYED OR UNVERIFIED", "Schwab has not confirmed current option-chain data.")
    meta = {"source": "Schwab", "symbol": symbol, "expiry": expiry, "delay_minutes": 0}
    rate = numeric(body.get("interestRate"))
    if rate is not None and -5 <= rate <= 30:
        meta["risk_free_rate"] = rate / 100
    entries = body.get(right + "ExpDateMap") or {}
    if not isinstance(entries, dict):
        raise FeedError("READ ERROR", "Schwab returned an unrecognized option-chain layout.")
    rows = []
    for bucket, strikes in entries.items():
        if str(bucket).split(":")[0] != expiry or not isinstance(strikes, dict):
            continue
        for items in strikes.values():
            if isinstance(items, list):
                for raw in items:
                    row = _schwab_row(raw, symbol, expiry, right)
                    if row is not None:
                        rows.append(row)
    return Snapshot(rows, meta, datetime.now(timezone.utc))


def live_feed(feed):
    if simulated_feed(feed):
        return Feed(error=FeedError("PREVIEW DATA", "Simulated records cannot supply a live contract workspace."))
    return feed


def contract_expirations(ctx):
    feed = ctx.get("expirations", symbol=ctx.ticker)
    if ctx.preview:
        return feed
    feed = live_feed(feed)
    if first_listed_dte(feed, ctx.ticker, ctx.session, ctx.now) is not None or not ctx.schwab_store:
        return feed
    return live_feed(ctx.schwab_store.get("option_expirations", symbol=ctx.ticker, paused=ctx.paused))


def reported_realtime(row, feed):
    if not feed.snapshot:
        return False
    values = [numeric(v) for v in (row.get("delay_minutes"), feed.snapshot.meta.get("delay_minutes")) if v is not None]
    return bool(values) and all(v == 0 for v in values)


def usable_chain(feed, ctx, expiry, right, *, broker_only=False):
    rows = fresh_option_rows(feed, ctx.ticker, expiry, ctx.now, paused=ctx.paused, max_age=ctx.option_max_age)
    for row in rows:
        if row.get("type") != right or broker_only and not reported_realtime(row, feed):
            continue
        try:
            spec = contract_spec(row, ctx.ticker)
        except (ValueError, TypeError):
            continue
        if min(spec.expires, spec.last_trade) > ctx.now:
            return True
    return False


def contract_chain(ctx, expiry, right, *, broker_only=False):
    expiry = str(expiry)
    if ctx.preview:
        return ctx.option_chain(expiry, right=right, near=False)

    candidates = []
    if ctx.has_webull_options:
        feed, label = ctx.option_chain(expiry, provider="Webull", right=right, near=False)
        feed = live_feed(feed)
        if usable_chain(feed, ctx, expiry, right, broker_only=broker_only):
            return feed, label
        candidates.append((feed, label))
    if ctx.schwab_store and ctx.ticker in SYMBOLS:
        feed = live_feed(ctx.schwab_store.get("option_chain", symbol=ctx.ticker, expiry=expiry,
                                              right=right, paused=ctx.paused))
        if usable_chain(feed, ctx, expiry, right, broker_only=broker_only):
            return feed, "Schwab"
        candidates.append((feed, "Schwab"))
    if broker_only:
        return next(((f, name) for f, name in candidates if f.rows),
                    candidates[0] if candidates else (Feed(error=FeedError("UNAVAILABLE", "Broker option quotes unavailable.")), "Broker"))
    feed, label = ctx.option_chain(expiry, provider="Tradytics", right=right, near=False)
    feed = live_feed(feed)
    if usable_chain(feed, ctx, expiry, right, broker_only=broker_only):
        return feed, label
    candidates.append((feed, label))
    # Preserve the selected provider's explanation when all live requests are unusable.
    for candidate in candidates:
        if candidate[0].rows:
            return candidate
    return next((candidate for candidate in candidates if candidate[0].error), candidates[0])
