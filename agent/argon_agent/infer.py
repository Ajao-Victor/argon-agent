"""Load pickles, build features, emit 1h/2h/8h ETH percent change."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from argon_agent.config import MODELS_DIR, pickle_path, pickle_url
from argon_agent.features import create_features, persistence_pct
from argon_agent.bars import StaleBarError, bar_hour_id, closed_bars_for_hour
from argon_agent.ingest import fetch_dia_spot, fetch_eth_hourly
from argon_agent.model_types import load_bundle
from argon_agent.policy import pct_from_prices, pred_price_from_log_return
from argon_agent.serialize import current_hour_id

log = logging.getLogger("argon.infer")


@dataclass
class HorizonPred:
    pct: float
    source: str  # lgbm | persistence | catchup


@dataclass
class Inference:
    eth_pct_1h: HorizonPred
    eth_pct_2h: HorizonPred
    eth_pct_8h: HorizonPred
    pred_eth_usd_8h: float
    spot_usd: float | None
    close: float
    bar_time: str
    bar_hour_id: int
    hour_id: int
    model_loaded: bool


def _ensure_pickle(horizon: str) -> Path | None:
    path = pickle_path(horizon)
    if path.exists():
        return path
    url = pickle_url(horizon)
    if not url:
        return None
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Downloading %s pickle from MODEL_%s_URL", horizon, horizon.upper())
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return path


def _align_row(latest: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    row = pd.DataFrame(index=latest.index)
    for c in columns:
        if c in latest.columns:
            row[c] = latest[c]
        else:
            row[c] = 0.0
    return row.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def _predict_bundle(bundle: dict, featured: pd.DataFrame) -> float:
    model = bundle["model"]
    scaler = bundle["scaler"]
    feature_columns = list(bundle["feature_columns"])
    model_cols = list(bundle.get("model_feature_cols") or getattr(model, "feature_cols", feature_columns))
    latest = featured.iloc[[-1]]
    X_all = _align_row(latest, feature_columns)
    X_scaled = pd.DataFrame(scaler.transform(X_all), columns=feature_columns, index=latest.index)
    use_cols = [c for c in model_cols if c in X_scaled.columns]
    log_return = float(model.predict(X_scaled[use_cols])[0])
    return log_return


def _horizon_from_pickle(horizon: str, featured: pd.DataFrame, ohlc: pd.DataFrame) -> HorizonPred:
    path = _ensure_pickle(horizon)
    if path is None:
        hours = int(horizon.replace("h", ""))
        log.warning("%s pickle missing at %s; using persistence", horizon, pickle_path(horizon))
        return HorizonPred(pct=persistence_pct(ohlc, hours), source="persistence")
    try:
        bundle = load_bundle(path)
        log_return = _predict_bundle(bundle, featured)
        pct = float((np.exp(log_return) - 1.0) * 100.0)
        log.info("%s pickle loaded from %s → %+.4f%%", horizon, path, pct)
        return HorizonPred(pct=pct, source="lgbm")
    except Exception:
        hours = int(horizon.replace("h", ""))
        if horizon == "8h":
            log.exception("Failed to run 8h pickle at %s", path)
            raise
        log.exception("Failed to run %s pickle; falling back to persistence", horizon)
        return HorizonPred(pct=persistence_pct(ohlc, hours), source="persistence")


def _eight_h_price(featured: pd.DataFrame, close: float) -> tuple[HorizonPred, float]:
    path = _ensure_pickle("8h")
    if path is None:
        raise RuntimeError(f"eth_8h_lgbm.pkl is required at {pickle_path('8h')} or set MODEL_8H_URL.")
    bundle = load_bundle(path)
    log_return = _predict_bundle(bundle, featured)
    pred = pred_price_from_log_return(close, log_return)
    pct = pct_from_prices(close, pred)
    log.info("8h pickle → price $%.2f (%+.4f%%) from bar close $%.2f", pred, pct, close)
    return HorizonPred(pct=pct, source="lgbm"), pred


def infer(days_back: int = 60, hour_id: int | None = None) -> Inference:
    hour_id = int(hour_id if hour_id is not None else current_hour_id())
    path_8h = pickle_path("8h")
    log.info("8h pickle path=%s exists=%s hour=%s", path_8h, path_8h.exists(), hour_id)
    if not path_8h.exists() and not pickle_url("8h"):
        raise RuntimeError(f"eth_8h_lgbm.pkl is required at {path_8h} or set MODEL_8H_URL.")
    ohlc = closed_bars_for_hour(fetch_eth_hourly(days_back=days_back), hour_id)
    featured = create_features(ohlc)
    if featured.empty:
        raise RuntimeError("Feature frame is empty — need more hourly bars")
    feature_hour = bar_hour_id(featured.index[-1])
    if feature_hour != hour_id - 1:
        raise StaleBarError(
            f"feature row {featured.index[-1]} is hour {feature_hour}; "
            f"need {hour_id - 1} (dropna dropped the live bar)"
        )
    close = float(ohlc["Close"].iloc[-1])
    p8, pred_eth_usd_8h = _eight_h_price(featured, close)
    p1 = _horizon_from_pickle("1h", featured, ohlc)
    p2 = _horizon_from_pickle("2h", featured, ohlc)
    spot, _ = fetch_dia_spot()
    bar_time = pd.Timestamp(ohlc.index[-1]).tz_convert("UTC").isoformat()
    log.info("hour %s using closed bar %s close=%.2f pred8h=$%.2f", hour_id, bar_time, close, pred_eth_usd_8h)
    return Inference(
        eth_pct_1h=p1,
        eth_pct_2h=p2,
        eth_pct_8h=p8,
        pred_eth_usd_8h=pred_eth_usd_8h,
        spot_usd=spot if spot is not None else close,
        close=close,
        bar_time=bar_time,
        bar_hour_id=hour_id - 1,
        hour_id=hour_id,
        model_loaded=True,
    )
