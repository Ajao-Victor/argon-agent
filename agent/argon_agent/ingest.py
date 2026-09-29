"""Tiingo hourly ETH bars + DIA spot. Token comes from TIINGO_API_KEY only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

from argon_agent.config import tiingo_api_key

TIINGO_URL = "https://api.tiingo.com/tiingo/crypto/prices"
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


def fetch_eth_hourly(days_back: int = 60) -> pd.DataFrame:
    key = tiingo_api_key()
    if not key:
        raise RuntimeError("TIINGO_API_KEY is not set")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)
    headers = {"Content-Type": "application/json", "Authorization": f"Token {key}"}
    params = {
        "tickers": "ethusd",
        "startDate": start.strftime("%Y-%m-%d"),
        "endDate": end.strftime("%Y-%m-%d"),
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
    df["traditional_log_return"] = np.log(df["Close"] / df["Close"].shift(1))
    return df
