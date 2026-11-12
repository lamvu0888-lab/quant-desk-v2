"""Plain-language interpretation of existing, rule-based Trade Plan outputs."""
from __future__ import annotations


def describe(plan, status):
    direction = str(plan.get('bias') or 'Mixed').lower()
    trigger = plan.get('trigger')
    invalidation = plan.get('invalidation')
    side = 'above' if direction == 'bullish' else 'below'
    opposite = 'below' if direction == 'bullish' else 'above'
    if status == 'ENTER SETUP':
        meaning = ('The 5-minute Trade Plan has reached its ready/triggered state. '
                   'This is a rules-based setup, not a guaranteed entry or order.')
    else:
        meaning = ('Price and options-flow evidence are developing in the same direction, '
                   'but the Trade Plan has not confirmed entry. Wait for confirmation.')
    if trigger is not None:
        meaning += f' Watch for a completed 5-minute candle {side} {trigger}.'
    if invalidation is not None:
        meaning += f' A completed 5-minute close {opposite} {invalidation} invalidates the setup.'
    if plan.get('divergence'):
        meaning += ' Warning: price and signed options premium diverge.'
    return meaning


def explain_evidence(plan):
    evidence = plan.get('evidence') or []
    output = []
    for item in evidence[:6]:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            output.append(f'{item[0]}: {item[1]}')
    return output


def discord_text(signal):
    from terminal_data import et_time
    from terminal_style import amount, compact, ratio_pct

    f = signal.get
    flow = f("flow") or {}
    model = f("model") or {}
    if signal["status"] == "MOVE WATCH":
        return (f"**{signal['symbol']} · PRICE MOVE WATCH · {f('direction')}**\n"
                f"Completed 5m close {et_time(f('bar'), short=True)} ET | {f('window')} change "
                f"{amount(f('previous_price'))} → {amount(f('price'))} ({f('change_pct'):+.2f}%)\n"
                "Price candles only; current quote and options-flow confirmation not verified. "
                "Check the live quote and Trade Plan before considering an entry.")
    if signal["status"] == "DATA QUALITY":
        return (f"**{signal['symbol']} · DATA QUALITY**\n"
                f"Detected {et_time(f('triggered_at_utc'))} ET | {f('scope', '—')}\n"
                f"Quote {et_time(f('quote_at'), short=True)} | Latest 5m close {et_time(f('bar'), short=True)} | "
                f"Flow {et_time(f('flow_at'), short=True)} ET\n"
                + str(f("explanation") or "Waiting for synchronized feeds.")[:500])
    lines = [f"**{signal['symbol']} · {signal['status']} · {signal['bias']}**",
             f"Detected {et_time(f('triggered_at_utc'))} | 5m close {et_time(f('bar'), short=True)} | 1m close {et_time(f('entry_bar'), short=True)} ET",
             f"Price {amount(f('price'))} | {f('scope', '—')} | {f('setup') or 'No directional setup'} | Evidence {f('score', '—')}/100"]
    if f("trigger") is not None:
        lines.extend([f"Trigger {amount(f('trigger'))} | Entry {amount(f('entry'))} | Stop {amount(f('stop'))} | Invalidation {amount(f('invalidation'))}",
                      f"TP1 {amount(f('tp1'))} ({amount(f('rr'))}R) | TP2 {amount(f('tp2'))} ({amount(f('rr2'))}R) | Hold {f('hold', '—')}m"])
    if f("movement") and f("flow_confirmation") and signal["status"] in ("ENTER LONG", "ENTER SHORT"):
        movement, confirmation = signal["movement"], signal["flow_confirmation"]
        lines.append(f"{movement['market']} · {movement['playbook']} | Closed 5m direction {movement['closed_bars']}/2 | Options flow {confirmation['count']}/2")
    lines.append(f"{f('regime') or 'Regime unavailable'} | 15m net premium {compact(flow.get('recent_net'), currency=True, signed=True)} | "
                 f"Flow acceleration {compact(flow.get('momentum'), currency=True, signed=True)}")
    lines.append(f"GEX {compact(f('gex'), currency=True, signed=True)} per 1% · {f('dealer_note') or 'Dealer context unavailable'}")
    lines.append(f"IV {ratio_pct(f('atm_iv'))} · {f('iv_valuation') or 'Unavailable'} | Data {f('confidence') or '—'}")
    if f("validation", "").startswith("Prospectively") and model.get("p_tp1") is not None:
        sample = f("validation_sample", "—")
        ev = f" | Estimated EV {model['ev_r']:+.2f}R" if f("expectancy_validation") and model.get("ev_r") is not None else ""
        lines.append(f"Model TP1 first {model['p_tp1']:.1%} · TP1 comparison n={sample}{ev}")
    else:
        if model.get("p_tp1") is not None and model.get("ev_r") is not None:
            lines.append(f"Monte Carlo research estimate · TP1 first {model['p_tp1']:.1%} | EV {model['ev_r']:+.2f}R")
        else:
            lines.append("Monte Carlo · comparable prospective validation pending.")
    if f("outcome"):
        result = signal["outcome"]
        lines.append(f"Paper exit {result['state']} at {amount(result['price'])} · {result['r']:+.2f}R after modeled cost.")
    if f("crossing"):
        cross = signal["crossing"]
        lines.append(f"5m close crossed {cross['direction']} {cross['level']} at {amount(cross['price'])}.")
    for cross in (f("aligned_crossings") or [])[:2]:
        lines.append(f"5m close crossed {cross['direction']} {cross['level']} at {amount(cross['price'])} with aligned options flow.")
    action = f("trigger_text") if signal["status"] in ("ENTER LONG", "ENTER SHORT") else None
    lines.append("Review · " + str(action or f("explanation") or "Follow the next completed price and flow observations.")[:230])
    if f("evidence"):
        lines.append("Agrees · " + " ".join(f("evidence")[:2])[:180])
    conflicts = list(dict.fromkeys((f("blocks") or []) + (f("conflicts") or []) + (f("missing") or [])))
    if conflicts:
        lines.append("Caution · " + " ".join(conflicts[:2])[:220])
    return "\n".join(lines)[:1900]
