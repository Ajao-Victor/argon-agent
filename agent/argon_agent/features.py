"""Feature builder that matches eth_hourly_prediction.FeatureEngineering (no TA-Lib)."""

from __future__ import annotations

import numpy as np
import pandas as pd

PREDICTION_HORIZON_HOURS = 8
LOOKBACK = [8, 12, 24, 48, 96, 168]


def create_features(data: pd.DataFrame) -> pd.DataFrame:
    df = data.copy()
    ret = df["traditional_log_return"]

    for lag in sorted({1, 2, 3, 4, PREDICTION_HORIZON_HOURS, 12, 24, 48, 72, 96, 168}):
        df[f"ret_lag_{lag}"] = ret.shift(lag)

    for window in sorted({4, PREDICTION_HORIZON_HOURS, 12, 24, 48}):
        df[f"ret_sum_{window}h"] = ret.rolling(window).sum()
        df[f"ret_sum_sign_{window}h"] = np.sign(df[f"ret_sum_{window}h"])
        df[f"ret_fade_{window}h"] = -df[f"ret_sum_{window}h"]

    for window in LOOKBACK:
        df[f"vol_std_{window}h"] = ret.rolling(window).std()
        df[f"vol_ewma_{window}h"] = ret.ewm(halflife=window / 2).std()
        df[f"realized_vol_{window}h"] = np.sqrt(ret.pow(2).rolling(window).sum())

    vol24 = df["vol_std_24h"].replace(0, np.nan)
    for window in sorted({4, PREDICTION_HORIZON_HOURS, 12, 24}):
        df[f"ret_volnorm_{window}h"] = df[f"ret_sum_{window}h"] / vol24

    df["ret_autocorr_8h"] = ret.rolling(168).corr(ret.shift(PREDICTION_HORIZON_HOURS))

    for window in LOOKBACK:
        df[f"sma_ret_{window}h"] = ret.rolling(window).mean()
        df[f"ema_ret_{window}h"] = ret.ewm(span=window).mean()

    for window in [24, 48, 168]:
        df[f"price_sma_{window}h"] = df["Close"].rolling(window).mean()
        df[f"price_ratio_{window}h"] = df["Close"] / df[f"price_sma_{window}h"]
        df[f"hl_ratio_{window}h"] = df["High"].rolling(window).max() / df["Low"].rolling(window).min()
        z_den = df["Close"].rolling(window).std().replace(0, np.nan)
        df[f"price_z_{window}h"] = (df["Close"] - df[f"price_sma_{window}h"]) / z_den

    df = _manual_technicals(df)
    df["bid_ask_spread"] = (df["High"] - df["Low"]) / df["Close"]
    df["volume_price_trend"] = df["Volume"] * ret
    for window in [24, 48]:
        df[f"volume_sma_{window}h"] = df["Volume"].rolling(window).mean()
        df[f"volume_ratio_{window}h"] = df["Volume"] / df[f"volume_sma_{window}h"]

    df["ret_rank_24h"] = ret.rolling(24).rank(pct=True)
    df["vol_rank_24h"] = df["vol_std_24h"].rolling(168).rank(pct=True)
    df["high_vol_regime"] = (df["vol_std_24h"] > df["vol_std_24h"].rolling(168).quantile(0.75)).astype(int)
    df["trend_strength"] = np.abs(df["sma_ret_24h"])

    df["hour"] = df.index.hour
    df["day_of_week"] = df.index.dayofweek
    df["is_weekend"] = (df.index.dayofweek >= 5).astype(int)
    df["is_us_cash"] = df.index.hour.isin(range(14, 21)).astype(int)
    df["is_asia"] = df.index.hour.isin(list(range(0, 8))).astype(int)
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["dow_sin"] = np.sin(2 * np.pi * df["day_of_week"] / 7)
    df["dow_cos"] = np.cos(2 * np.pi * df["day_of_week"] / 7)
    return df.dropna()


def _manual_technicals(df: pd.DataFrame) -> pd.DataFrame:
    for window in [14, 24]:
        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(window).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window).mean()
        rs = gain / loss.replace(0, np.nan)
        df[f"rsi_{window}h"] = 100 - (100 / (1 + rs))
    sma = df["Close"].rolling(48).mean()
    std = df["Close"].rolling(48).std()
    df["bb_upper"] = sma + 2 * std
    df["bb_lower"] = sma - 2 * std
    df["bb_position"] = (df["Close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])
    ema12 = df["Close"].ewm(span=12).mean()
    ema26 = df["Close"].ewm(span=26).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9).mean()
    df["macd_histogram"] = df["macd"] - df["macd_signal"]
    return df


def persistence_pct(ohlc: pd.DataFrame, hours: int) -> float:
    """Realized last-N-hour % change. Used only when a dedicated pickle is missing."""
    close = ohlc["Close"]
    if len(close) <= hours:
        raise ValueError(f"need >{hours} bars for persistence")
    log_ret = float(np.log(close.iloc[-1] / close.iloc[-1 - hours]))
    return float((np.exp(log_ret) - 1.0) * 100.0)
