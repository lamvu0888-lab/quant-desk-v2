"""Read-only, sampled ES/MES flow for comparison with SPX. No inferred flow authorizes entries."""
from collections import deque
from datetime import timedelta
import re

from market_session import ET, rth_bounds
from terminal_data import numeric, positive, time_or_none
from v3_provenance import simulated_feed

MULTIPLIER = {"ES": 50, "MES": 5}
MINUTE = timedelta(minutes=1)
MAX_GAP = timedelta(seconds=12)
MAX_SPREAD_AGE = timedelta(seconds=5)
SOURCE = "Schwab sampled futures quotes"
QUALITY = "Estimated · sampled last trades"
FIELDS = ("es", "mes", "composite", "webull")


def sign(value):
    return int(value > 0) - int(value < 0)


def parse_quote(body, root, received, bounds):
    """Only use positively identified, live, active quarterlies in the cash comparison session."""
    obj = body.get("/" + root)
    if not isinstance(obj, dict) or obj.get("assetMainType") != "FUTURE" or obj.get("realtime") is not True:
        return None
    ref, q = obj.get("reference"), obj.get("quote")
    if not isinstance(ref, dict) or not isinstance(q, dict) or ref.get("futureIsActive") is False:
        return None
    code = str(ref.get("futureActiveSymbol", "")).upper()
    if not re.fullmatch("/" + root + r"[HMUZ]\d{2}", code) or obj.get("symbol") not in ("/"+root, code):
        return None
    quarter_month = dict(H=3, M=6, U=9, Z=12)[code[-3]]
    year = 2000 + int(code[-2:])
    receipt_day = received.astimezone(ET).date()
    months = (year - receipt_day.year) * 12 + quarter_month - receipt_day.month
    if not 0 <= months <= 12:
        return None
    multiplier = numeric(ref.get("futureMultiplier"))
    if multiplier is not None and multiplier != MULTIPLIER[root]:
        return None
    expiry = time_or_none(ref.get("futureExpirationDate"))
    if expiry is not None and expiry.date() < received.astimezone(ET).date():
        return None
    at, quote_at = time_or_none(q.get("tradeTime")), time_or_none(q.get("quoteTime"))
    last, size, total = positive(q.get("lastPrice")), positive(q.get("lastSize")), numeric(q.get("totalVolume"))
    if (not at or not bounds[0] <= at < bounds[1] or not timedelta(0) <= received - at <= MAX_GAP
            or not last or not size or total is None or total < size or not size.is_integer() or not total.is_integer()):
        return None
    bid, ask = positive(q.get("bidPrice")), positive(q.get("askPrice"))
    bid_at = time_or_none(q.get("bidTime")) or quote_at
    ask_at = time_or_none(q.get("askTime")) or quote_at
    return dict(contract=code, last=last, size=size, total=total, at=at, received=received,
                bid=bid, ask=ask, bid_at=bid_at, ask_at=ask_at)


def side(previous, current):
    """Quote test with a strictly pre-trade spread; tick estimate only for a price change."""
    at = current["at"]
    bid, ask = previous["bid"], previous["ask"]
    if (bid and ask and bid < ask and
            all(t and timedelta(0) < at - t <= MAX_SPREAD_AGE for t in (previous["bid_at"], previous["ask_at"]))):
        if current["last"] >= ask:
            return 1
        if current["last"] <= bid:
            return -1
    return sign(current["last"] - previous["last"])


class SampledFutures:
    """One instance per credential-backed feed store. FeedStore serializes this single request key."""
    def __init__(self):
        self.day = None
        self.previous = {}
        self.buckets = {}

    def _reset(self, root):
        self.previous.pop(root, None)
        self.buckets[root] = deque(maxlen=400)

    def observe(self, body, received):
        day = received.astimezone(ET).date()
        bounds = rth_bounds(day)
        if self.day != day:
            self.day = day
            for root in MULTIPLIER:
                self._reset(root)
        if not bounds or not bounds[0] <= received <= bounds[1]:
            return {}
        result = {}
        for root in MULTIPLIER:
            sample = parse_quote(body, root, received, bounds)
            if not sample:
                self._reset(root)
                continue
            self._append(root, sample)
            result[root] = {"contract": sample["contract"], "last_trade": sample["at"].isoformat(),
                            "points": self._completed(root, received)}
        return result

    def _append(self, root, sample):
        prev = self.previous.get(root)
        if prev and (prev["contract"] != sample["contract"] or prev["total"] > sample["total"]
                     or sample["at"] < prev["at"] or sample["received"] - prev["received"] > MAX_GAP):
            self._reset(root)
            prev = None
        self.previous[root] = sample
        minute = sample["at"].replace(second=0, microsecond=0)
        series = self.buckets[root]
        if not series or series[-1]["minute"] != minute:
            series.append(dict(minute=minute, first=sample["at"], last=sample["at"], close=sample["last"],
                               signed=0., classified=0., seen=0., samples=0))
        bucket = series[-1]
        bucket.update(last=sample["at"], close=sample["last"])
        # The cumulative-volume change across a minute boundary cannot be split reliably.
        if not prev or prev["at"] < minute:
            return
        increment = sample["total"] - prev["total"]
        bucket["seen"] += increment
        if increment < sample["size"] or sample["at"] == prev["at"]:
            return
        direction = side(prev, sample)
        if direction:
            # A multi-print volume increment has just one last print. Count that print only.
            bucket["signed"] += direction * sample["size"]
            bucket["classified"] += sample["size"]
            bucket["samples"] += 1

    def _completed(self, root, received):
        points, cumulative, last_end = [], 0., None
        for b in self.buckets[root]:
            end = b["minute"] + MINUTE
            if end > received:
                break
            coverage = b["classified"] / b["seen"] if b["seen"] else 0.
            # These minimums qualify a research observation, never a trading signal.
            if (b["first"] - b["minute"] > MAX_GAP or end - b["last"] > MAX_GAP
                    or b["samples"] < 3 or coverage < .02):
                cumulative, last_end = 0., None
                continue
            if last_end != b["minute"]:
                cumulative = 0.
            cumulative += b["signed"]
            last_end = end
            points.append({"time": end.isoformat(), "close": b["close"], "delta": b["signed"],
                           "cvd": cumulative, "classified": b["classified"], "observed_volume": b["seen"],
                           "coverage": coverage, "samples": b["samples"], "imbalance": b["signed"]/b["classified"]})
        return points


def leg_features(series, target):
    if not series:
        return None
    points = series["points"]
    if not points or time_or_none(points[-1]["time"]) != target:
        return None
    streak = [points[-1]]
    for point in reversed(points[:-1]):
        if time_or_none(streak[-1]["time"]) - time_or_none(point["time"]) != MINUTE:
            break
        streak.append(point)
    last = streak[0]
    change_pct = (100 * (last["close"] / streak[1]["close"] - 1) if len(streak) >= 2 else None)
    velocity = last["delta"]  # d(sampled cumulative signed volume) / one minute
    acceleration = velocity - streak[1]["delta"] if len(streak) >= 2 else None
    direction = sign(last["imbalance"])
    persistence = sum(sign(p["delta"]) == direction for p in streak[:3])/3 if direction and len(streak)>=3 else None
    divergence, absorption, reversal = None, None, None
    if len(streak) >= 3:
        move = last["close"] - streak[2]["close"]
        pressure = sum(p["delta"] for p in streak[:3])/sum(p["classified"] for p in streak[:3])
        divergence = bool(abs(pressure)>=.2 and sign(move) == -sign(pressure))
        absorption = bool(abs(pressure)>=.5 and abs(move)<=.25)
        reversal = bool(sign(streak[1]["delta"]) == sign(streak[2]["delta"]) == -direction
                        and direction and direction == sign(last["close"] - streak[1]["close"]))
    return dict(contract=series["contract"], time=last["time"], last_trade=series["last_trade"], cvd=last["cvd"],
                change_pct=change_pct,
                velocity=velocity, acceleration=acceleration, imbalance=last["imbalance"], samples=last["samples"],
                coverage=last["coverage"], classified=last["classified"], observed_volume=last["observed_volume"],
                persistence=persistence, divergence=divergence, possible_absorption=absorption, possible_reversal=reversal)


def context(feed, target, now, *, include_history=False):
    if not target or not timedelta(0)<=now-target<MINUTE or not feed or feed.error or not feed.snapshot or simulated_feed(feed):
        return None
    if (feed.snapshot.meta.get("source") != "Schwab" or feed.snapshot.meta.get("delay_minutes") != 0
            or not timedelta(0) <= now - feed.snapshot.received_at <= MAX_GAP):
        return None
    legs = {}
    for root in MULTIPLIER:
        series = feed.obj.get(root)
        stamp = time_or_none(series.get("last_trade")) if series else None
        if stamp and timedelta(0)<=now-stamp<=MAX_GAP:
            leg = leg_features(series, target)
            if leg:
                leg["classified_notional"] = leg["classified"] * MULTIPLIER[root] * series["points"][-1]["close"]
                if include_history:
                    leg["history"] = series["points"]
                legs[root] = leg
    if not legs:
        return None
    es, mes = legs.get("ES"), legs.get("MES")
    composite = composite_cvd = composite_acceleration = None
    window_minutes = 0
    agreement = "Waiting for both contracts"
    if es and mes and es["contract"][-3:] == mes["contract"][-3:]:
        composite = (es["imbalance"] + mes["imbalance"])/2
        es_path = {p["time"]: p["imbalance"] for p in feed.obj["ES"]["points"]}
        mes_path = {p["time"]: p["imbalance"] for p in feed.obj["MES"]["points"]}
        cursor = target
        normalized = []
        while cursor.isoformat() in es_path and cursor.isoformat() in mes_path:
            key = cursor.isoformat()
            normalized.append((es_path[key] + mes_path[key]) / 2)
            cursor -= MINUTE
        window_minutes = len(normalized)
        composite_cvd = sum(normalized)
        composite_acceleration = normalized[0] - normalized[1] if window_minutes >= 2 else None
        signs = (sign(es["imbalance"]), sign(mes["imbalance"]))
        agreement = "Aligned" if signs[0]==signs[1]!=0 else "Disagree" if signs[0]*signs[1]<0 else "Mixed"
    elif es and mes:
        agreement = "Different delivery months"
    confidence = min(leg["coverage"] for leg in legs.values())
    return {"source":SOURCE, "quality":QUALITY, "at":target.isoformat(), "legs":legs,
            "classified_fraction_min":confidence, "confidence_basis":"Sample coverage; aggressor side inferred",
            "primary":"ES" if es else "MES", "composite":composite, "composite_cvd":composite_cvd,
            "composite_acceleration":composite_acceleration, "paired_minutes":window_minutes,
            "agreement":agreement, "shadow":True}


def proxy_caption(proxy):
    if not proxy or not proxy.get("shadow"):
        return ""
    root = proxy.get("primary")
    leg = (proxy.get("legs") or {}).get(root)
    if not leg or numeric(leg.get("cvd")) is None or numeric(leg.get("coverage")) is None:
        return ""
    return (f"Schwab {leg['contract']} estimated futures CVD {leg['cvd']:+,.0f} sampled contracts · "
            f"{leg['coverage']:.0%} classified volume in sampled intervals · ES/MES {proxy['agreement'].lower()} · research only")


def fetch_context(ctx, close, *, include_history=False):
    if ctx.ticker != "SPX" or ctx.preview or ctx.paused or not getattr(ctx, "schwab_store", None) or not close:
        return None
    bounds = rth_bounds(ctx.session)
    if not bounds or not bounds[0] <= ctx.now < bounds[1]:
        return None
    return context(request_feed(ctx.schwab_store), close, ctx.now, include_history=include_history)


def request_feed(store):
    if store and getattr(getattr(store, "client", None), "supports_futures_proxy", False) is True:
        return store.get("futures_proxy")
    return None


def comparison(cases):
    """Identical forward, nonambiguous SPX origins for all methods, including flats."""
    paired = []
    coverage = {key:0 for key in FIELDS}
    for row in cases:
        if row.get("symbol") != "SPX" or row.get("outcome") not in ("up","down","neither"):
            continue
        f = row.get("features") or {}
        values = [numeric(f.get("proxy_"+key)) for key in FIELDS]
        for k,v in zip(FIELDS,values):
            coverage[k] += int(v is not None)
        if all(v is not None for v in values):
            paired.append((values, row["label"]))
    out = {}
    for i,k in enumerate(FIELDS):
        votes = [(sign(v[i]),truth) for v,truth in paired if v[i]!=0]
        good = sum(side==truth for side,truth in votes)
        out[k] = {"calls":len(votes),"correct":good,"directional_precision":good/len(votes) if votes else None}
    return {"paired_origins":len(paired),"coverage":coverage,"methods":out}
