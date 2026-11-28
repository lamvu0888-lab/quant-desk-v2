"""Keep display fixtures, broker sandbox data and live research in separate paths."""
from collections.abc import Mapping

DISPLAY_SOURCES = {"demo", "preview", "synthetic", "sandbox"}


def display_only(metadata):
    if not isinstance(metadata, Mapping):
        return False
    return any(metadata.get(k) for k in ("preview", "synthetic", "sandbox", "demo")) or str(
        metadata.get("source", "")).strip().lower() in DISPLAY_SOURCES


def simulated_feed(feed):
    snap = getattr(feed, "snapshot", None)
    return bool(snap and display_only(snap.meta))


def simulated_context(ctx, read):
    return (getattr(ctx, "preview", False) or str(getattr(ctx, "price_provider", "")).lower() in DISPLAY_SOURCES
            or any(simulated_feed(read.get(k)) for k in ("bars", "flow", "gamma_feed", "vol")))


def display_plan(plan):
    return display_only(plan) or display_only((plan or {}).get("movement_v3"))


def live_signal(sig):
    """V3 snapshots always carried an explicit preview flag, including RC1."""
    return bool(sig and sig.get("preview") is False and not display_only(sig)
                and sig.get("source") in ("Webull", "Schwab", "Tradytics"))


def live_record(row):
    payload = row.get("payload", {})
    return not display_only(row) and not display_only(payload) and live_signal(payload.get("signal"))
