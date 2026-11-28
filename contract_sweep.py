"""Strike-sensitive option projection at one fixed valuation time."""
import math

import numpy as np
import plotly.graph_objects as go

from market_session import ET
from terminal_data import positive
from v3_options import YEAR, greeks


def price_range(spot, strike, target, years, iv, levels=()):
    # One diffusion standard deviation at the evaluation horizon, with an intraday
    # floor so an immediate mark still includes prices on both sides of spot.
    width = max(.0025 * spot, abs(target - spot), spot * iv * math.sqrt(max(0., years)))
    anchors = [spot, strike, target, *(v for v in levels if positive(v) and abs(v-spot) <= 3 * width)]
    low = max(.01, min(spot-width, *anchors) - width*.08)
    high = max(spot+width, *anchors) + width*.08
    return low, high


def sweep_grid(low, high, step=None, anchors=()):
    if not (np.isfinite([low,high]).all() and 0 < low < high):
        raise ValueError("Set a positive underlying minimum below the maximum.")
    if step is None:
        grid = np.linspace(low, high, 101)
    else:
        if not np.isfinite(step) or step <= 0 or (high-low)/step > 500:
            raise ValueError("Use a positive price increment with at most 500 intervals.")
        grid = low + np.arange(int((high-low)/step)+1) * step
    return np.unique([*grid, high, *(v for v in anchors if positive(v) and low <= v <= high)])


def price_figure(spec, grid, spot, target, at, iv, rate, dividend, entry, qty, fee, spread,
                 *, normalize=False, extra=(), levels=None):
    from v3_views import AXIS_LABELS, PRICE_COLORS, _display_values, _marker_labels, _scenario_layout

    if not 0 < iv <= 5 or at > spec.expires:
        raise ValueError("Choose a supported volatility and evaluation time within the contract lifetime.")
    keys = list(dict.fromkeys(["premium", "delta", "gamma", "theta", *extra]))
    years = (spec.expires-at).total_seconds()/YEAR
    stats = greeks(spec, grid, years, iv, rate, dividend,
                   extended=any(k in keys for k in ("vanna","charm")))
    data = {**stats, "underlying":grid}
    width = spread/2 if at < spec.expires and at <= spec.last_trade else 0.
    pnl = (np.maximum(0., data["premium"]-width)-entry) * spec.multiplier * qty - 2 * qty * fee
    stamp = at.astimezone(ET).strftime("%b %d, %Y %H:%M %Z")
    fig = go.Figure()
    for i,key in enumerate(keys):
        raw = np.asarray(data[key],float)
        finite = raw[np.isfinite(raw)]
        scale = (finite.min(),finite.max()) if normalize and finite.size else None
        label = AXIS_LABELS[key]
        hover = "Underlying $%{x:,.2f}<br>"+label+" %{customdata[0]:.4g}"
        if key=="premium":
            hover+=f"<br>{stamp} · {years*YEAR/3600:.2f} h left"
            if years>0:
                hover+=f"<br>Scenario IV {iv:.2%}"
            hover+="<br>Modeled net P&L $%{customdata[1]:,.2f}"
        fig.add_scatter(x=grid, y=_display_values(raw,scale),
                        yaxis="y" if normalize or i==0 else f"y{i+1}", name=label, mode="lines",
                        line=dict(color=PRICE_COLORS[key],width=2,dash="dash"),
                        customdata=np.column_stack((raw,pnl)), hovertemplate=hover+"<extra>Scenario</extra>")
    labels = {}
    for name,level in (("Spot",spot),("Strike",spec.strike),("Target",target),*(levels or {}).items()):
        if positive(level) and grid[0] <= level <= grid[-1]:
            labels.setdefault(float(level),[]).append(name)
    shapes=[]
    lanes=_marker_labels(fig,shapes,labels,lambda x:(x-grid[0])/(grid[-1]-grid[0]))
    _scenario_layout(fig,keys,data,normalize,shapes,lanes,f"sweep:{spec.code}:{normalize}:{grid[0]:.4f}:{grid[-1]:.4f}")
    fig.update_xaxes(title_text="Underlying price ($)", range=[grid[0],grid[-1]], tickformat=",.2f",
                     nticks=4, tickfont_size=10, title_font_size=10, showspikes=True,
                     spikemode="across",spikesnap="cursor",spikethickness=1,spikecolor="#b0bdcc")
    return fig
