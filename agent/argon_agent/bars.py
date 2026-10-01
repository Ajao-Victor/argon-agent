"""Bind an inference to the just-closed UTC hour. Never use a previous-day bar."""

from __future__ import annotations

import pandas as pd


class StaleBarError(RuntimeError):
    """Last closed ETH hour is not this hour's prior bar (previous-day carry)."""


def bar_hour_id(ts) -> int:
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return int(stamp.timestamp() // 3600)


def closed_bars_for_hour(ohlc: pd.DataFrame, hour_id: int) -> pd.DataFrame:
    """Keep bars that have closed before `hour_id`. Last bar must be hour_id-1."""
    if ohlc.empty:
        raise StaleBarError(f"no ETH bars for hour {hour_id}")
    frame = ohlc.copy()
    idx = frame.index
    if getattr(idx, "tz", None) is None:
        frame.index = idx.tz_localize("UTC")
    else:
        frame.index = idx.tz_convert("UTC")
    cutoff = pd.Timestamp(hour_id * 3600, unit="s", tz="UTC")
    closed = frame[frame.index < cutoff]
    if closed.empty:
        raise StaleBarError(f"no closed ETH bars before hour {hour_id}")
    last = bar_hour_id(closed.index[-1])
    expected = hour_id - 1
    if last != expected:
        raise StaleBarError(
            f"ETH bar {closed.index[-1]} is hour {last}; need closed hour {expected} "
            f"(refusing previous-day / stale carry into {hour_id})"
        )
    return closed
