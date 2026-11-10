"""Build a chart-ready price-level snapshot from validated market inputs."""
from __future__ import annotations

import json
from dataclasses import asdict

from desk_analytics import PriceLevel
from terminal_data import numeric, positive, time_or_none

GAMMA_LEVELS = {"call_wall": "Call wall", "put_wall": "Put wall", "flip": "Gamma flip", "max_pain": "Max pain"}
PINE_COLORS = {"support": "#46ccb0", "resistance": "#ff7489", "dealer": "#f5bf69", "gex_pos": "#5eccaa",
               "gex_neg": "#f38497", "dark": "#a99cff", "expected": "#7aa9fc"}


def dealer_levels(obj, spot, strike_count):
    stamp = time_or_none(obj.get("time"))
    stamp = stamp.isoformat() if stamp else None
    values = obj.get("levels") if isinstance(obj.get("levels"), dict) else {}
    if not obj.get("rows") and numeric(obj.get("total")) is None:
        return []
    levels = [PriceLevel(label, price, "dealer", stamp) for key, label in GAMMA_LEVELS.items()
              if (price := positive(values.get(key)))]
    if spot and strike_count:
        strikes = [(strike, net) for row in obj.get("rows", []) if isinstance(row, dict)
                   and (strike := positive(row.get("strike"))) and abs(strike / spot - 1) <= .03
                   and (net := numeric(row.get("net"))) is not None and net != 0]
        for strike, net in sorted(strikes, key=lambda value: abs(value[1]), reverse=True)[:strike_count]:
            label = f"GEX {'+' if net > 0 else '-'} {strike:g}"
            levels.append(PriceLevel(label, strike, "gex_pos" if net > 0 else "gex_neg", stamp, net))
    return levels


def dark_levels(rows, at, limit=4):
    levels = [(price, dollars) for row in rows if (price := positive(row.get("price")))
              and (dollars := positive(row.get("notional_usd")))]
    return [PriceLevel(f"Dark pool {i}", price, "dark", at, dollars)
            for i, (price, dollars) in enumerate(sorted(levels, key=lambda p: -p[1])[:limit], start=1)]


def expected_levels(vol, spot=None):
    move = vol.get("expected_move")
    if not isinstance(move, dict):
        return []
    width, center = positive(move.get("move_usd")), positive(vol.get("underlying_price")) or positive(spot)
    if not width or not center or center <= width:
        return []
    return [PriceLevel("Expected move high", center + width, "expected"),
            PriceLevel("Expected move low", center - width, "expected")]


def export_payload(symbol, session, expiry, as_of, levels, *, preview=False, price_source="Tradytics",
                   gamma=None):
    return {"schema": 1, "symbol": symbol, "session": str(session), "expiry": expiry,
            "as_of_utc": as_of.isoformat(), "demo": preview, "price_source": price_source,
            "options_source": "Tradytics" if not preview else "Illustrative",
            "net_gex_usd_per_1pct_move": numeric(gamma), "levels": [asdict(level) for level in levels]}


def pine_script(payload):
    """TradingView reads the embedded values when the indicator is added."""
    # Labels and symbol are constructed by the app, but quote them for Pine as well.
    def quote(value):
        return json.dumps(str(value), ensure_ascii=False)
    snapshot = time_or_none(payload["as_of_utc"])
    if snapshot is None or not payload["levels"]:
        raise ValueError("A timestamped snapshot with at least one level is required")
    started = int(snapshot.timestamp() * 1000)
    symbol = payload["symbol"]
    prefix = "DEMO " if payload["demo"] else ""
    name = quote(f"{prefix}Quant Desk Levels - {symbol}")
    lines = [
        "//@version=6",
        f"indicator({name}, overlay=true, max_lines_count=50, max_labels_count=50)",
        f"// Snapshot {snapshot.isoformat()} | Session {payload['session']} | Expiry {payload['expiry'] or 'All DTE'}",
        "// Exported snapshot. Refresh in Quant Desk and replace this script for newer prices.",
        f"int snapshotTime = {started}",
        "bool showNames = input.bool(true, \"Show price labels\")",
        "var array<line> guides = array.new<line>()",
        "var array<label> names = array.new<label>()",
        "drawLevel(float price, string title, color tint) =>",
        "    line guide = line.new(snapshotTime, price, snapshotTime + 60000, price, xloc=xloc.bar_time, extend=extend.right, color=tint, style=line.style_dashed)",
        "    array.push(guides, guide)",
        "    if showNames",
        "        label nameTag = label.new(math.max(time, snapshotTime), price, title + \"  \" + str.tostring(price, format.mintick), xloc=xloc.bar_time, style=label.style_label_left, color=color.new(tint, 75), textcolor=tint, size=size.small)",
        "        array.push(names, nameTag)",
        f"if barstate.islast and str.upper(syminfo.ticker) == {quote(symbol)}",
        "    while array.size(guides) > 0",
        "        line.delete(array.pop(guides))",
        "    while array.size(names) > 0",
        "        label.delete(array.pop(names))",
    ]
    grouped = {}
    for level in payload["levels"][:40]:
        price = positive(level.get("price"))
        if price is None or level["kind"] not in PINE_COLORS:
            continue
        label = level["label"]
        if level["kind"] in ("gex_pos", "gex_neg") and (net := numeric(level.get("strength"))) is not None:
            label += f" ({net / 1_000_000:+.1f}M GEX)"
        _, names = grouped.setdefault(f"{price:.8f}", (PINE_COLORS[level["kind"]], []))
        names.append(label)
    for price, (color, names) in grouped.items():
        lines.append(f"    drawLevel({price}, {quote(' / '.join(names))}, {color})")
    return "\n".join(lines) + "\n"
