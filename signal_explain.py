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
    from terminal_style import amount

    f = signal.get
    if signal["status"] == "MOVE WATCH":
        return (f"**{signal['symbol']} · {'📈' if f('direction') == 'UP' else '📉'} Move watch**\n"
                f"{f('window')} move {f('change_pct'):+.2f}% · {amount(f('previous_price'))} → {amount(f('price'))}\n"
                f"{et_time(f('bar'), short=True)} ET · Price only. Check live quote and Trade Plan.")
    if signal["status"] == "EXIT":
        outcome = f("outcome") or {}
        return (f"**{signal['symbol']} · Paper setup closed**\n"
                f"{outcome.get('state', 'Exit')} at {amount(outcome.get('price') or f('price'))} · "
                f"{et_time(f('triggered_at_utc'), short=True)} ET\n"
                "Paper tracking only; no order was placed.")
    if signal["status"] in ("ENTER LONG", "ENTER SHORT"):
        side = "📈 Long setup" if signal["status"] == "ENTER LONG" else "📉 Short setup"
        return (f"**{signal['symbol']} · {side}**\n"
                f"Price {amount(f('price'))} · Trigger {amount(f('trigger'))} · {et_time(f('triggered_at_utc'), short=True)} ET\n"
                f"Stop {amount(f('stop'))} · Target {amount(f('tp1'))}\n"
                f"{f('setup') or 'Trade Plan'} · Review live option quote and risk before acting.")
    return f"**{signal['symbol']} · {signal['status']}**\nCheck the dashboard for details."
