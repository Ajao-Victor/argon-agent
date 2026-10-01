"""Coinbase hourly ETH bars + DIA spot. No API key for the candles."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from argon_agent.bars import append_bars

log = logging.getLogger("argon.ingest")

COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/ETH-USD/candles"
COINBASE_PAGE_HOURS = 300
DIA_URL = (
    "https://api.diadata.org/v1/assetQuotation/Ethereum/"
    "0x0000000000000000000000000000000000000000"
)


def fetch_dia_spot() -> tuple[float | None, str | None]:
    try:
        resp = requests.get(DIA_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return float(data["Price"]), str(data.get("Time") or "")
    except Exception:
        return None, None


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coinbase_page(start: datetime, end: datetime) -> pd.DataFrame:
    resp = requests.get(
        COINBASE_CANDLES,
        params={"granularity": 3600, "start": _iso(start), "end": _iso(end)},
        headers={"User-Agent": "argon-agent"},
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Coinbase {resp.status_code}: {resp.text[:200]}")
    rows = []
    for candle in resp.json():
        opened = datetime.fromtimestamp(int(candle[0]), tz=timezone.utc)
        rows.append(
            {
                "Date": pd.Timestamp(opened),
                "Open": float(candle[3]),
                "High": float(candle[2]),
                "Low": float(candle[1]),
                "Close": float(candle[4]),
                "Volume": float(candle[5]),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    return pd.DataFrame(rows).set_index("Date").sort_index()


def fetch_coinbase_hourly(hours: int = 48) -> pd.DataFrame:
    """Public ETH-USD hourly candles. Coinbase returns at most 300 bars per call."""
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    floor = end - timedelta(hours=max(hours, 1))
    frames: list[pd.DataFrame] = []
    cursor = end
    while cursor > floor:
        page_start = max(floor, cursor - timedelta(hours=COINBASE_PAGE_HOURS))
        frames.append(_coinbase_page(page_start, cursor))
        cursor = page_start
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        raise RuntimeError("Coinbase returned no hourly candles")
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def fetch_eth_hourly(days_back: int = 60) -> pd.DataFrame:
    hours = max(int(days_back) * 24, 200)
    df = append_bars(fetch_coinbase_hourly(hours=hours), None)
    if df.empty:
        raise RuntimeError("No ETH bars after cleaning")
    log.info("Coinbase ETH bars %s → %s (%s rows)", df.index[0], df.index[-1], len(df))
    return df
