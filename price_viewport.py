"""Local Plotly renderer for the Overview chart. No third-party script host is required."""
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import plotly
import streamlit as st
import streamlit.components.v1 as components


@st.cache_resource(show_spinner=False)
def _component():
    # Serve the same Plotly version that created the figure, without duplicating its
    # megabyte-sized JS in every Streamlit refresh or committing a vendor bundle.
    assets = TemporaryDirectory(prefix="quantdesk-chart-")
    target = Path(assets.name)
    for name in ("index.html", "viewport.js", "viewport.css"):
        shutil.copyfile(Path(__file__).with_suffix("") / name, target / name)
    installed = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
    shutil.copyfile(installed, target / "plotly.min.js")
    render = components.declare_component("market_price_viewport", path=str(target))
    return render, assets


def render_price_chart(figure, ticker, interval, *, preview, provider):
    render, _ = _component()
    scope = f"{'preview' if preview else 'live'}:{provider}:{ticker}:{interval}"
    render(figure=figure.to_json(), scope=scope, key="price_action_chart",
           default=None, height=int(figure.layout.height) + 74, tab_index=0)
