"""Prospective comparisons for independently gated standard and early paper tracks."""
from collections import defaultdict
from statistics import mean

from early_flow import EARLY, STANDARD
from gemini_advisor import BASELINE
from terminal_data import numeric, time_or_none


def score_band(score):
    if score is None or not 60 <= score <= 100:
        return "Outside 60–100"
    return "90–100" if score >= 90 else f"{int(score // 10) * 10}–{int(score // 10) * 10 + 9}"


def summarize(signals, trades, group, slip):
    groups = defaultdict(lambda: {"signals": 0, "trades": [], "delays": []})

    def key(mode, plan):
        score = numeric(plan.get("score"))
        if group == "Former score gate":
            bucket = ("Unknown" if score is None else "Below 60" if score < 60 else
                      "Newly eligible (60–70)" if score <= 70 else "Previously eligible (71+)")
        elif group == "Volatility regime":
            bucket = plan.get("regime") or "Unknown"
        elif group == "Market / setup":
            bucket = " / ".join([plan.get("movement", {}).get("market") or "Unknown", plan.get("setup") or "Unknown"])
        else:
            bucket = score_band(score)
        return mode, bucket

    for signal in signals:
        group_values = groups[key(signal["mode"], signal["plan"])]
        group_values["signals"] += 1
        at, trigger = time_or_none(signal["origin"]), time_or_none(signal["plan"].get("entry_bar"))
        if at and trigger and at >= trigger:
            group_values["delays"].append((at-trigger).total_seconds())
    for trade in trades:
        track = trade.get("track", BASELINE)
        if track in (BASELINE, EARLY):
            groups[key(EARLY if track == EARLY else STANDARD, trade["plan"])]["trades"].append(trade)
    rows = []
    for (mode, bucket), values in sorted(groups.items()):
        cohort = values["trades"]
        closed = [t for t in cohort if t["state"] == "CLOSED"]
        net = [sum(f["pnl"] for f in t["fills"]) - 200 * slip * t["qty"] for t in closed]
        def avg_field(field):
            samples = [t[field] for t in closed if t.get(field) is not None]
            return round(mean(samples), 2) if samples else None
        failed = sum(any(f["reason"] in ("STOP", "20% PREMIUM STOP") for f in t["fills"])
                     and not any(f["reason"] == "TP1" for f in t["fills"]) for t in closed)
        rows.append({"Mode": mode, group: bucket, "Signals": values["signals"],
                     "Mean trigger-to-scan seconds": round(mean(values["delays"]), 1) if values["delays"] else None,
                     "Filled": sum(bool(t.get("opened")) for t in cohort),
                     "Skipped": sum(t["state"] == "SKIPPED" for t in cohort), "Closed": len(net),
                     "Win %": round(100 * sum(p > 0 for p in net) / len(net), 1) if net else None,
                     "Stop before TP1 %": round(100 * failed / len(net), 1) if net else None,
                     "Mean net P/L (empirical EV $)": round(mean(net), 2) if net else None,
                     "Mean MAE $/contract": avg_field("mae_per_contract"),
                     "Mean MFE $/contract": avg_field("mfe_per_contract"),
                     "Excursion samples": sum("mae_per_contract" in t for t in closed)})
    return rows


def paired_entries(signals, trades):
    # Match either time order, within the original ten-minute setup cooldown.
    # Never substitute futures/ETF prices for cash SPX, or compare different options.
    def key(row):
        p = row["plan"]
        return (row["symbol"], p.get("session"), p.get("source"), p.get("expiry"),
                p.get("direction"), p.get("setup"), p.get("trigger"))
    positions = {row["id"]: row for row in trades}
    early = defaultdict(list)
    standard = []
    for row in sorted(signals, key=lambda r: r["origin"]):
        (early[key(row)] if row["mode"] == EARLY else standard).append(row)
    pairs = []
    for row in standard:
        at = time_or_none(row["origin"])
        candidates = early[key(row)]
        if not candidates or not at:
            continue
        other = min(candidates, key=lambda r: abs((at - time_or_none(r["origin"])).total_seconds()))
        delta = (at - time_or_none(other["origin"])).total_seconds()
        if abs(delta) > 600:
            continue
        candidates.remove(other)
        a, b = positions.get(other["id"], {}), positions.get(row["id"], {})
        side = row["plan"]["direction"]
        spot_a, spot_b = numeric(a.get("spot")), numeric(b.get("spot"))
        same = a.get("contract") and a.get("contract") == b.get("contract")
        pairs.append({"Symbol": row["symbol"], "Setup": row["plan"]["setup"], "Standard UTC": row["origin"],
                      "Early lead (seconds)": round(delta, 1),
                      "Early spot improvement (points)": round(side * (spot_b - spot_a), 3)
                      if spot_a is not None and spot_b is not None else None,
                      "Early premium improvement ($/contract)": round(100*(b["entry_ask"]-a["entry_ask"]),2)
                      if same else None})
    return pairs
