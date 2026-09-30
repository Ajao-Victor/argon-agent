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
from argon_agent.ingest import fetch_dia_spot, fetch_eth_hourly
from argon_agent.model_types import load_bundle

log = logging.getLogger("argon.infer")


@dataclass
class HorizonPred:
    pct: float
    source: str  # lgbm | persistence


@dataclass
class Inference:
    eth_pct_1h: HorizonPred
    eth_pct_2h: HorizonPred
    eth_pct_8h: HorizonPred
    spot_usd: float | None
    close: float
    bar_time: str
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
    return float((np.exp(log_return) - 1.0) * 100.0)


def _horizon_from_pickle(horizon: str, featured: pd.DataFrame, ohlc: pd.DataFrame) -> HorizonPred:
    path = _ensure_pickle(horizon)
    if path is None:
        hours = int(horizon.replace("h", ""))
        log.warning("%s pickle missing at %s; using persistence", horizon, pickle_path(horizon))
        return HorizonPred(pct=persistence_pct(ohlc, hours), source="persistence")
    try:
        bundle = load_bundle(path)
        pct = _predict_bundle(bundle, featured)
        log.info("%s pickle loaded from %s → %+.4f%%", horizon, path, pct)
        return HorizonPred(pct=pct, source="lgbm")
    except Exception:
        hours = int(horizon.replace("h", ""))
        if horizon == "8h":
            log.exception("Failed to run 8h pickle at %s", path)
            raise
        log.exception("Failed to run %s pickle; falling back to persistence", horizon)
        return HorizonPred(pct=persistence_pct(ohlc, hours), source="persistence")


def infer(days_back: int = 60) -> Inference:
    path_8h = pickle_path("8h")
    log.info("8h pickle path=%s exists=%s", path_8h, path_8h.exists())
    if not path_8h.exists() and not pickle_url("8h"):
        raise RuntimeError(f"eth_8h_lgbm.pkl is required at {path_8h} or set MODEL_8H_URL.")
    ohlc = fetch_eth_hourly(days_back=days_back)
    featured = create_features(ohlc)
    if featured.empty:
        raise RuntimeError("Feature frame is empty — need more hourly bars")
    p8 = _horizon_from_pickle("8h", featured, ohlc)
    p1 = _horizon_from_pickle("1h", featured, ohlc)
    p2 = _horizon_from_pickle("2h", featured, ohlc)
    spot, _ = fetch_dia_spot()
    close = float(ohlc["Close"].iloc[-1])
    bar_time = str(ohlc.index[-1])
    return Inference(
        eth_pct_1h=p1,
        eth_pct_2h=p2,
        eth_pct_8h=p8,
        spot_usd=spot if spot is not None else close,
        close=close,
        bar_time=bar_time,
        model_loaded=True,
    )
