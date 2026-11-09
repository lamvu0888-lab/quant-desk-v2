"""Deterministic, clearly labeled illustrative data for exploring the terminal offline."""
from __future__ import annotations

import math
import zlib
from datetime import datetime, timedelta, timezone

import numpy as np

from market_session import ET, calendar, rth_bounds
from tradytics_feed import Feed, Snapshot

DEMO_AT = datetime(2026, 11, 6, 17, 16, 20, tzinfo=timezone.utc)
NAMES = {"SPY": "SPDR S&P 500 ETF Trust", "SPX": "S&P 500 Index", "QQQ": "Invesco QQQ Trust",
         "IWM": "iShares Russell 2000 ETF", "NVDA": "NVIDIA Corporation", "TSLA": "Tesla, Inc.",
         "AVGO": "Broadcom Inc.", "NDX": "Nasdaq 100 Index", "VIX": "Cboe Volatility Index"}
PRICES = {"SPY": 760, "SPX": 7610, "QQQ": 678, "IWM": 248, "NVDA": 185, "TSLA": 425,
          "AVGO": 335, "NDX": 25120, "RUT": 2435, "DJI": 47352, "VIX": 17.5}
GREEKS = {"gamma": "usd_per_1pct_move", "delta": "shares", "vanna": "usd_per_vol_point", "charm": "shares_per_day"}


class DemoStore:
    def __init__(self, ticker):
        self.ticker = ticker
        self.day = DEMO_AT.astimezone(ET).date()
        rng = np.random.default_rng(zlib.crc32(ticker.encode()))
        days = [day.date().isoformat() for day in calendar().sessions_in_range("2026-10-29", str(self.day))][-5:]
        self.bars = []
        price = PRICES.get(ticker, 105.0) * .994
        for d, day in enumerate(days):
            start, end = rth_bounds(day)
            for i in range(min(int((end - start).total_seconds() // 60), int((DEMO_AT - start).total_seconds() // 60))):
                stamp = start + timedelta(minutes=i)
                delta = .000013 + .00013 * math.sin(i / 22 + d) + float(rng.normal(0, .00028))
                close = price * math.exp(delta)
                spread = price * float(rng.uniform(.000045, .00023))
                self.bars.append({
                    "time": stamp.isoformat(), "open": round(price, 4), "high": round(max(price, close) + spread, 4),
                    "low": round(min(price, close) - spread, 4), "close": round(close, 4),
                    "volume": None if ticker in ("SPX", "NDX", "RUT", "DJI", "VIX") else int(rng.integers(55000, 320000)),
                    "vwap": None if ticker in ("SPX", "NDX", "RUT", "DJI", "VIX") else round((price + close) / 2, 4),
                })
                price = close
        self.spot = round(price, 2)
        self.expiries = [str(self.day + timedelta(days=days)) for days in (0, 3, 7, 14, 42)]
        self.step = 5 if self.spot >= 1000 else 2.5 if ticker in ("NVDA", "TSLA") else 1
        self.center = round(self.spot / self.step) * self.step

    def quote(self, symbol):
        spot = self.spot if symbol == self.ticker else PRICES.get(symbol, 105)
        change = .64 if symbol not in ("VIX", "IWM") else -.38
        return {"symbol": symbol, "name": NAMES.get(symbol, symbol), "price": spot, "change_pct": change,
                "previous_close": round(spot / (1 + change / 100), 2), "session": "regular", "stale": False,
                "date": str(self.day), "price_at": DEMO_AT.replace(second=0).isoformat(), "time": DEMO_AT.replace(second=0).isoformat(),
                "volume": 37212300, "relative_volume": 1.37}

    def exposure(self, greek, by, dte, expiry):
        weights = [.51, .11, .16, .09, .13]
        selected = [(e, w) for e, w in zip(self.expiries, weights)
                    if (expiry is None or expiry == e) and (dte is None or (datetime.fromisoformat(e).date() - self.day).days <= dte)]
        rows = []
        scale = {"gamma": 4.1e8, "delta": 9e6, "vanna": 7e6, "charm": 4.8e5}[greek]
        net_profile = []
        for i in range(-24, 25):
            calls = scale * (1.12 * math.exp(-((i - 4) / 3.8) ** 2) + .22 * math.exp(-((i - 13) / 5) ** 2))
            puts = -scale * (.87 * math.exp(-((i + 4) / 3.4) ** 2) + .12 * math.exp(-((i + 12) / 6) ** 2))
            net = calls + puts
            if greek == "vanna":
                net = -scale * math.exp(-((i - 2) / 4.4) ** 2) + scale * .42 * math.exp(-((i + 7) / 4) ** 2)
            if greek == "charm":
                net = scale * math.exp(-((i + 2) / 4) ** 2) - scale * .49 * math.exp(-((i - 8) / 3.6) ** 2)
            net_profile.append((i, calls, puts, net))
        factor = sum(w for _, w in selected)
        if by == "expiry":
            for e, w in selected:
                rows.append({"expiry": e, "net": round(sum(r[3] for r in net_profile) * w)})
        else:
            for i, calls, puts, net in net_profile:
                rows.append({"strike": self.center + self.step * i, "net": round(net * factor),
                             "call": round(calls * factor) if greek in ("gamma", "delta") else None,
                             "put": round(puts * factor) if greek in ("gamma", "delta") else None})
        return {"symbol": self.ticker, "greek": greek, "unit": GREEKS[greek], "model": "Illustrative positioning profile",
                "time": (DEMO_AT - timedelta(minutes=1, seconds=20)).isoformat(), "date": str(self.day), "spot": self.spot,
                "total": sum(r["net"] for r in rows) if selected else None, "rows": rows if selected else [],
                "levels": {"call_wall": self.center + 4 * self.step, "put_wall": self.center - 4 * self.step,
                           "flip": self.center - self.step, "max_pain": self.center}}

    def flow_timeline(self, interval, dte):
        start, _ = rth_bounds(str(self.day))
        minutes = int(interval.removesuffix("m")) if interval in ("1m", "5m", "15m") else 60
        factor = .54 if dte == 0 else .83 if dte == 7 else 1
        rows = []
        for i in range(int((DEMO_AT - start).total_seconds() // 60) // minutes):
            call = round(factor * minutes / 5 * (270000 + 1050000 * math.sin(i * minutes / 29) + 210000 * math.cos(i * minutes / 5)))
            put = round(factor * minutes / 5 * (-630000 + 690000 * math.cos(i * minutes / 34)))
            rows.append({"time": (start + timedelta(minutes=i * minutes)).isoformat(), "date": str(self.day),
                         "call_premium_usd": call, "put_premium_usd": put, "net_premium_usd": call - put,
                         "put_call_ratio": .82 + .21 * math.sin(i / 5), "prints": 36 + i % 17})
        return rows

    def options_tape(self):
        rows = []
        for i in range(90):
            expiry = self.expiries[i % len(self.expiries)]
            kind = ("sweep", "block", "single", "split")[i % 4]
            option_type = "call" if i % 4 != 2 else "put"
            strike = self.center + (i % 13 - 6) * self.step
            price = round(.77 + (i % 9) * .41, 2)
            size = 300 + (89 - i) * 29
            rows.append({"id": 10600 - i, "time": (DEMO_AT - timedelta(seconds=13 + 38*i)).isoformat(),
                         "symbol": self.ticker, "type": option_type, "strike": strike, "expiry": expiry,
                         "dte": (datetime.fromisoformat(expiry).date() - self.day).days, "price": price, "size": size,
                         "contract": f"{self.ticker}{expiry[2:].replace('-', '')}{option_type[0].upper()}{int(strike*1000):08d}",
                         "premium_usd": round(price * size * 100), "side": ("bought", "sold", "bought", "mid")[i % 4],
                         "side_checked": i > 1, "kind": kind, "condition": "spread" if i % 7 == 3 else None,
                         "opening": i % 7 == 0, "open_interest": 2400 + i * 211, "spot": self.spot})
        return rows

    def get(self, name, **params):
        if name == "quotes":
            data = [self.quote(symbol) for symbol in params.get("symbol", self.ticker).split(",")]
        elif name == "indices":
            data = [self.quote(symbol) for symbol in ("SPX", "NDX", "RUT", "DJI", "VIX")]
        elif name == "bars":
            if params.get("timeframe") == "1d":
                data = []
                for i, d in enumerate(calendar().sessions_in_range("2026-08-05", "2026-11-05")):
                    base = self.spot * (.965 + i * .00053 + .006 * math.sin(i / 3.6))
                    data.append({"time": d.tz_localize("UTC").isoformat(), "open": base * .998, "high": base * 1.005,
                                 "low": base * .995, "close": base, "volume": 30000000 + i*251000})
            else:
                data = self.bars
        elif name == "breadth":
            data = {"up": 648, "down": 391, "total": 1068, "strong_up": 54, "strong_down": 28,
                    "equal_weight_change_pct": .43, "cap_weight_change_pct": .64}
        elif name == "exposure":
            data = self.exposure(params.get("greek", "gamma"), params.get("by", "strike"), params.get("dte"), params.get("expiry"))
        elif name == "timeline":
            data = self.flow_timeline(params.get("interval", "5m"), params.get("dte"))
        elif name in ("flow", "chain"):
            data = self.options_tape()
            if name == "chain":
                data = [{**row, "bid": round(row["price"] - .03, 2), "ask": round(row["price"] + .03, 2), "iv": .164 + i * .0015,
                         "delta": (-1 if row["type"] == "put" else 1) * (.14 + (i % 17) / 30), "gamma": .02, "theta": -.12,
                         "vega": .045, "volume": 1700 + i*32, "updated_at": DEMO_AT.isoformat()} for i, row in enumerate(data)]
            for key in ("type", "kind", "side", "expiry"):
                if params.get(key):
                    data = [row for row in data if row.get(key) == params[key]]
            dte = params.get("dte", params.get("max_dte"))
            if dte is not None:
                data = [row for row in data if row["dte"] <= dte]
            if params.get("min_premium"):
                data = [row for row in data if row["premium_usd"] >= params["min_premium"]]
        elif name == "volatility":
            data = {"date": str(self.day), "symbol": self.ticker, "underlying_price": self.spot, "atm_iv_30d": .164,
                    "iv_rank": .24, "iv_percentile": .37, "realized_vol_20d": .136, "realized_vol_30d": .142, "vrp": .022,
                    "expected_move": {"expiry": self.expiries[1], "dte": 3, "move_usd": round(self.spot * .0088, 2)},
                    "term": [{"expiry": e, "atm_iv": .134 + .009*i} for i, e in enumerate(self.expiries)],
                    "skew": [{"expiry": e, "put_25d_iv": .167 + .008*i, "call_25d_iv": .135 + .009*i} for i, e in enumerate(self.expiries)]}
        elif name == "dark_levels":
            data = [{"price": round(self.center + self.step * i, 2), "notional_usd": 1020000000 / (1 + abs(i) * .22),
                     "prints": 12 + 2*(8 - abs(i))} for i in range(-8, 9)]
        elif name == "dark_prints":
            data = [{"id": 30000 - i, "time": (DEMO_AT - timedelta(minutes=2*i + 1)).isoformat(), "symbol": self.ticker,
                     "price": round(self.spot - .001 * self.spot * math.sin(i), 2), "size": 65000 + (i % 9)*27000,
                     "notional_usd": (65000 + (i % 9)*27000) * self.spot, "kind": "trade", "venue": "dark_pool", "rank": i*3 + 2}
                    for i in range(24)]
            data = [row for row in data if row["notional_usd"] >= params.get("min_notional", 0)]
        elif name in ("signals", "signal"):
            signals = [{**row, "id": 700 + i, "fired_at": row["time"], "date": str(self.day), "status": "open",
                        "source": "repeat-flow" if i % 2 == 0 else "heavy-calls",
                        "pattern": "Repeat flow" if i % 2 == 0 else "Heavy calls", "title": "Illustrative signal",
                        "body": "Example of a recurring flow pattern with a quoted entry and subsequent option marks.",
                        "ask": row["price"] + .04, "return": .02 + i * .013, "best_return": .18}
                       for i, row in enumerate(self.options_tape()[4:12])]
            if name == "signals":
                data = [row for row in signals if row["status"] == params.get("status", "open") and row["source"] == params.get("source", row["source"])]
            else:
                selected = next((row for row in signals if row["id"] == params.get("id")), {})
                data = {**selected, "prints": self.options_tape()[4:9], "marks": [
                    {"time": (DEMO_AT - timedelta(minutes=35-5*i)).isoformat(), "value": round(1.8 + i*.031 + .07*math.sin(i), 2)}
                    for i in range(8)]}
        elif name == "performance":
            data = [{"pattern": label, "window": params.get("window", "30d"), "signals": count, "settled": count-4,
                     "win_rate": win, "average_return": win-.43, "median_return": win-.48}
                    for label, count, win in (("All patterns", 220, .52), ("Repeat flow", 96, .56), ("Heavy calls", 75, .49), ("ITM puts", 49, .51))]
        else:
            data = []
            for i, d in enumerate(calendar().sessions_in_range("2026-09-15", str(self.day))):
                data.append({"date": d.date().isoformat(), "close": self.spot * (.966 + i*.0009), "net_gamma_usd": (math.sin(i/3) + .45)*1.1e9,
                             "net_delta": (math.cos(i/4) + .7)*1.5e7, "atm_iv_30d": .167 + .025*math.sin(i/7),
                             "realized_vol_20d": .148 + .019*math.cos(i/6), "dark_notional_usd": (2.5 + math.sin(i/2))*1e9})
        return Feed(Snapshot(data, {"date": str(self.day), "as_of": DEMO_AT.isoformat()}, DEMO_AT))
