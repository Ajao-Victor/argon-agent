"""JSON the Vercel app consumes. Keep this free of web3 / LightGBM imports."""

from __future__ import annotations

from datetime import datetime, timezone

from argon_agent.config import MODEL_ID_TEXT
from argon_agent.policy import pct_to_bps, tripped_horizons


def current_hour_id(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return int(now.timestamp() // 3600)


def row_to_api(row: dict, *, warmup_complete: bool | None = None, now_hour: int | None = None) -> dict:
    if warmup_complete is None:
        warmup_complete = row.get("action") != "warmup"
    hour_id = int(row["hour_id"])
    live_hour = current_hour_id() if now_hour is None else int(now_hour)
    bar_hour = row.get("bar_hour_id")
    return {
        "hourId": hour_id,
        "targetHourId": int(row["target_hour_id"]),
        "submittedAt": _iso(row.get("submitted_at")),
        "predEthUsd8h": _f(row.get("pred_eth_usd_8h")),
        "barCloseUsd": _f(row.get("bar_close_usd")),
        "expectedEthUsd1h": _f(row.get("expected_eth_usd_1h")),
        "ethPct1h": float(row["eth_pct_1h"]),
        "ethPct2h": float(row["eth_pct_2h"]),
        "ethPct8h": float(row["eth_pct_8h"]),
        "ethPct1hSource": row.get("eth_pct_1h_source") or "lgbm",
        "ethPct2hSource": row.get("eth_pct_2h_source") or "lgbm",
        "ethPct8hSource": row.get("eth_pct_8h_source") or "lgbm",
        "spotUsd": _f(row.get("spot_usd")),
        "modelId": row.get("model_id") or MODEL_ID_TEXT,
        "status": row.get("status"),
        "realizedPctChange": _f(row.get("realized_pct_change")),
        "realizedSpotUsd": _f(row.get("realized_spot_usd")),
        "action": row.get("action"),
        "gate1hBps": 100,
        "gate2hBps": 250,
        "gate8hBps": 200,
        "warmupComplete": bool(warmup_complete),
        "forecastHash": row.get("forecast_hash"),
        "txHash": row.get("tx_hash"),
        "txHashRh": row.get("tx_hash_rh"),
        "rebalanceTx": row.get("rebalance_tx"),
        "rebalanceTxRh": row.get("rebalance_tx_rh"),
        "poolStatusArb": row.get("pool_status_arb"),
        "poolStatusRh": row.get("pool_status_rh"),
        "barTime": _iso(row.get("bar_time")),
        "barHourId": int(bar_hour) if bar_hour is not None else None,
        "live": hour_id == live_hour,
        "trippedHorizons": tripped_horizons(
            pct_to_bps(row["eth_pct_1h"]),
            pct_to_bps(row["eth_pct_2h"]),
            pct_to_bps(row["eth_pct_8h"]),
        ),
    }


def _iso(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _f(value):
    if value is None:
        return None
    return float(value)
