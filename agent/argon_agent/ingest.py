"""Tiingo hourly ETH bars + DIA spot. Token comes from TIINGO_API_KEY only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging

import pandas as pd
import requests

from argon_agent.bars import append_bars, bar_hour_id
from argon_agent.config import tiingo_api_key

log = logging.getLogger("argon.ingest")

TIINGO_URL = "https://api.tiingo.com/tiingo/crypto/prices"
COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/ETH-USD/candles"
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


def _fetch_tiingo_hourly(days_back: int) -> pd.DataFrame:
    key = tiingo_api_key()
    if not key:
        raise RuntimeError("TIINGO_API_KEY is not set")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)
    headers = {"Content-Type": "application/json", "Authorization": f"Token {key}"}
    # Full timestamps. A date-only endDate is midnight and drops today's hours.
    params = {
        "tickers": "ethusd",
        "startDate": _iso(start),
        "endDate": _iso(end),
        "resampleFreq": "1hour",
        "token": key,
    }
    resp = requests.get(TIINGO_URL, headers=headers, params=params, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Tiingo {resp.status_code}: {resp.text[:300]}")
    payload = resp.json()
    if not payload:
        raise RuntimeError("Tiingo returned empty payload")
    price_data = payload[0]["priceData"] if isinstance(payload, list) else payload["priceData"]
    rows = [
        {
            "Date": pd.to_datetime(p["date"], utc=True),
            "Open": p["open"],
            "High": p["high"],
            "Low": p["low"],
            "Close": p["close"],
            "Volume": p["volume"],
        }
        for p in price_data
    ]
    df = pd.DataFrame(rows).set_index("Date").sort_index().dropna()
    if df.empty:
        raise RuntimeError("No ETH bars after cleaning")
    return df


def fetch_coinbase_hourly(hours: int = 48) -> pd.DataFrame:
    """Public ETH-USD hourly candles. Fills the tail when Tiingo stops early."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
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
        raise RuntimeError("Coinbase returned no hourly candles")
    return pd.DataFrame(rows).set_index("Date").sort_index()


def fetch_eth_hourly(days_back: int = 60) -> pd.DataFrame:
    df = _fetch_tiingo_hourly(days_back)
    need = int(datetime.now(timezone.utc).timestamp() // 3600) - 1
    last = bar_hour_id(df.index[-1])
    if last < need:
        gap_hours = min(300, max(48, need - last + 2))
        log.warning("Tiingo last hour %s; need %s. Filling %s hours from Coinbase.", last, need, gap_hours)
        extra = fetch_coinbase_hourly(hours=gap_hours)
        df = append_bars(df, extra)
        log.info("ETH bars now through %s", df.index[-1])
    else:
        df = append_bars(df, None)
    if df.empty:
        raise RuntimeError("No ETH bars after cleaning")
    return df
