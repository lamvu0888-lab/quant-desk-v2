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
    def fmt(value):
        return 'Not available' if value is None else str(value)
    lines = [f"**{signal['symbol']} | {signal['status']} | {signal['bias']}**",
             f"**Signal time (UTC):** {signal['triggered_at_utc']}",
             f"**Observed price:** {fmt(signal.get('price'))} | **5m completed bar:** {fmt(signal.get('bar'))}",
             f"**Evidence score:** {fmt(signal.get('score'))}/100 (NOT win probability)",
             f"**Trade Plan state:** {fmt(signal.get('state'))} | **Options scope:** {fmt(signal.get('scope'))}",
             f"**Trigger:** {fmt(signal.get('trigger'))} | **Invalidation:** {fmt(signal.get('invalidation'))}",
             f"**What it means:** {signal.get('explanation', '')}"]
    if signal.get('evidence'):
        lines.append('**Supporting checks:** ' + ' | '.join(signal['evidence'][:4]))
    lines.append('**Safety:** Informational only. No order submitted. Verify quotes, spreads, and risk before trading.')
    return '\n'.join(lines)[:1900]
