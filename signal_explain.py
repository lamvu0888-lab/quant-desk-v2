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
    if signal["status"] == "V3 WATCH":
        return f"**{signal['symbol']} · {signal['movement_state']}** · {amount(f('price'))}\n{signal['explanation']} · Research warning only."
    if signal["status"] == "EMA TREND":
        up = f("direction") == "UP"
        next_step = "Watch for EMA 9 to hold on a pullback." if up else "Watch for a bounce to reject at EMA 9."
        return (f"**{signal['symbol']} · EMA 9/21 trend {'up' if up else 'down'}**\n"
                f"{amount(f('price'))} · 3m close {et_time(f('bar'), short=True)} ET · {f('source', 'Market')}\n"
                f"{next_step}")
    if signal["status"] == "MOVE WATCH":
        return (f"**{signal['symbol']} · {'📈' if f('direction') == 'UP' else '📉'} Move watch**\n"
                f"{f('window')} move {f('change_pct'):+.2f}% · {amount(f('previous_price'))} → {amount(f('price'))}\n"
                f"{et_time(f('bar'), short=True)} ET · {f('source', 'Market')} candles · Price only")
    if signal["status"] == "EXIT":
        outcome = f("outcome") or {}
        return (f"**{signal['symbol']} · Paper setup closed**\n"
                f"{outcome.get('state', 'Exit')} at {amount(outcome.get('price') or f('price'))} · "
                f"{et_time(f('triggered_at_utc'), short=True)} ET\n"
                "Paper tracking only; no order was placed.")
    if signal["status"] in ("ENTER LONG", "ENTER SHORT"):
        side = "📈 Long setup" if signal["status"] == "ENTER LONG" else "📉 Short setup"
        quote = f("option_quote") or {}
        confirmation = (f"{quote['source']} {amount(quote['strike'])} {quote['right']} · "
                        f"{amount(quote['bid'])} bid / {amount(quote['ask'])} ask · Flow confirmed") if quote.get("ready") else "Price and flow confirmed"
        recommendation = f("contract_recommendation") or {}
        chosen = recommendation.get("selected")
        if chosen and recommendation.get("status") == "qualified":
            confirmation = (
                f"{chosen['label']} · {chosen['source']} · bid/ask {amount(chosen['bid'])}/{amount(chosen['ask'])}\n"
                f"Modeled target/stop premium {amount(chosen['target_premium'])}/{amount(chosen['stop_premium'])} · "
                f"{recommendation['hold']}m · {chosen['quantity']} paper contract(s), debit up to ${chosen['cost']:,.2f}"
            )
            if recommendation.get("daily_capacity") is not None:
                confirmation += f" · modeled daily loss capacity at scan ${recommendation['daily_capacity']:,.2f}"
            elif recommendation.get("daily_capacity_verified") is False:
                confirmation += " · paper buying size unverified"
        if f("futures_cvd"):
            from schwab_futures import proxy_caption
            proxy = proxy_caption(f("futures_cvd"))
            if proxy:
                confirmation += f"\n{proxy}"
        if box := f("range_reversal"):
            edge_name = "Support bounce" if f("direction") == 1 else "Resistance rejection"
            provider = f"{f('source')} fallback" if f("price_fallback") else f("source")
            return (f"**{signal['symbol']} · Range reversal · {signal['status']}**\n"
                    f"{edge_name} {amount(box['edge'])} · Entry {amount(f('entry'))} · {provider} price\n"
                    f"Stop {amount(f('stop'))} · T1 {amount(f('tp1'))} · T2 {amount(f('tp2'))}\n"
                    f"{f('score')}/100 · {confirmation} · {et_time(f('triggered_at_utc'), short=True)} ET")
        return (f"**{signal['symbol']} · {side}**\n"
                f"Price {amount(f('price'))} · Trigger {amount(f('trigger'))} · {et_time(f('triggered_at_utc'), short=True)} ET\n"
                f"Stop {amount(f('stop'))} · Target {amount(f('tp1'))} · Evidence {f('score')}/100\n"
                f"{confirmation}")
    return f"**{signal['symbol']} · {signal['status']}**\nCheck the dashboard for details."
