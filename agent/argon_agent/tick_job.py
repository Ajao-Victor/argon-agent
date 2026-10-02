"""One UTC hour: infer → store → submit → rebalance."""

from __future__ import annotations

import logging
import time

from argon_agent.config import MODEL_ID_TEXT, WARMUP_SUBMITS
from argon_agent.db import Store, now_iso
from argon_agent.hashing import forecast_hash, hex_hash
from argon_agent.policy import (
    action_name,
    allowed_action,
    average_open_slice,
    pct_from_prices,
    pct_to_bps,
    user_action,
)
from argon_agent.serialize import current_hour_id, row_to_api

log = logging.getLogger("argon.tick")

INFER_ATTEMPTS = 8
INFER_RETRY_SECS = 12


def _apply_signer_gates(
    store: Store, hour_id: int, pct1h_bps: int, pct2h_bps: int, pct8h_bps: int, warmup_complete: bool
) -> None:
    for gate in store.list_gates():
        action, in_position = user_action(
            pct1h_bps,
            pct2h_bps,
            pct8h_bps,
            gate,
            in_position=bool(gate.get("in_position")),
            warmup_complete=warmup_complete,
        )
        store.save_gate_decision(str(gate["address"]), action, hour_id, in_position)
        log.info("signer %s hour %s action=%s in=%s", gate["address"], hour_id, action, in_position)


def _infer_live(hour_id: int):
    from argon_agent.bars import StaleBarError
    from argon_agent.infer import infer

    last_err: Exception | None = None
    for attempt in range(1, INFER_ATTEMPTS + 1):
        try:
            return infer(hour_id=hour_id)
        except StaleBarError as exc:
            last_err = exc
            log.warning("hour %s stale bar attempt %s/%s: %s", hour_id, attempt, INFER_ATTEMPTS, exc)
            if attempt < INFER_ATTEMPTS:
                time.sleep(INFER_RETRY_SECS)
    assert last_err is not None
    raise last_err


def run_hour(store: Store | None = None) -> dict:
    from argon_agent import chain as chainmod

    store = store or Store()
    store.ensure_schema()
    hour_id = current_hour_id()
    existing = store.get(hour_id)
    if existing:
        log.info("hour %s already stored (live)", hour_id)
        warmup = store.count() >= WARMUP_SUBMITS
        return row_to_api(existing, warmup_complete=warmup)

    prediction = _infer_live(hour_id)
    prior_1h = store.get(hour_id - 1)
    open_calls: list[tuple[float, int]] = []
    for row in store.list_recent(24):
        target = row.get("pred_eth_usd_8h")
        if target is None:
            continue
        remaining = int(row["target_hour_id"]) - hour_id
        if remaining >= 1:
            open_calls.append((float(target), remaining))
    open_calls.append((float(prediction.pred_eth_usd_8h), 8))
    averaged_1h = average_open_slice(open_calls, prediction.close, 1)
    averaged_2h = average_open_slice(open_calls, prediction.close, 2)
    from argon_agent.infer import HorizonPred

    if averaged_1h is not None:
        prediction.eth_pct_1h = HorizonPred(pct=averaged_1h, source="residual")
    if averaged_2h is not None:
        prediction.eth_pct_2h = HorizonPred(pct=averaged_2h, source="residual")
    prediction.eth_pct_8h = HorizonPred(
        pct=pct_from_prices(prediction.close, prediction.pred_eth_usd_8h),
        source="lgbm",
    )
    expected_1h = prediction.close * (1.0 + prediction.eth_pct_1h.pct / 100.0)
    if (
        prior_1h
        and int(prior_1h["hour_id"]) != hour_id
        and prior_1h.get("pred_eth_usd_8h") is not None
        and abs(float(prior_1h["pred_eth_usd_8h"]) - prediction.pred_eth_usd_8h) < 1e-6
    ):
        raise RuntimeError(
            f"hour {hour_id} 8h predicted price ${prediction.pred_eth_usd_8h:.2f} "
            f"matches hour {prior_1h['hour_id']} (refusing to restamp the previous 8h price)"
        )
    pct1h_bps = pct_to_bps(prediction.eth_pct_1h.pct)
    pct2h_bps = pct_to_bps(prediction.eth_pct_2h.pct)
    pct8h_bps = pct_to_bps(prediction.eth_pct_8h.pct)
    fhash = forecast_hash(hour_id, pct1h_bps, pct2h_bps, pct8h_bps)

    live = chainmod.clients()
    in_pool = chainmod.any_in_pool(live)
    db_count = store.count()
    onchain_count = None
    if live:
        onchain_count = live[0].forecast_count()
    if onchain_count is None:
        onchain_count = chainmod.registry_forecast_count()
    # Vault warmup is on-chain forecastCount >= 9. Postgres dry-run rows do not count.
    if onchain_count is not None:
        warmup_complete = onchain_count + 1 >= WARMUP_SUBMITS
    else:
        warmup_complete = db_count + 1 >= WARMUP_SUBMITS

    action_code = allowed_action(
        pct1h_bps, pct2h_bps, pct8h_bps, in_pool=in_pool, warmup_complete=warmup_complete
    )
    action = action_name(action_code, warmup_complete)
    _apply_signer_gates(store, hour_id, pct1h_bps, pct2h_bps, pct8h_bps, warmup_complete)

    store.upsert(
        {
            "hour_id": hour_id,
            "target_hour_id": hour_id + 8,
            "submitted_at": now_iso(),
            "eth_pct_1h": prediction.eth_pct_1h.pct,
            "eth_pct_2h": prediction.eth_pct_2h.pct,
            "eth_pct_8h": prediction.eth_pct_8h.pct,
            "eth_pct_1h_source": prediction.eth_pct_1h.source,
            "eth_pct_2h_source": prediction.eth_pct_2h.source,
            "eth_pct_8h_source": prediction.eth_pct_8h.source,
            "spot_usd": prediction.spot_usd,
            "model_id": MODEL_ID_TEXT,
            "status": "pending",
            "realized_pct_change": None,
            "realized_spot_usd": None,
            "action": action,
            "forecast_hash": hex_hash(fhash),
            "tx_hash": None,
            "tx_hash_rh": None,
            "rebalance_tx": None,
            "rebalance_tx_rh": None,
            "pool_status_arb": None,
            "pool_status_rh": None,
            "bar_time": prediction.bar_time,
            "bar_hour_id": prediction.bar_hour_id,
            "pred_eth_usd_8h": prediction.pred_eth_usd_8h,
            "bar_close_usd": prediction.close,
            "expected_eth_usd_1h": expected_1h,
        }
    )
    store.mature(hour_id, prediction.spot_usd)

    chain_result = chainmod.execute_hour(
        live,
        hour_id,
        pct1h_bps,
        pct2h_bps,
        pct8h_bps,
        fhash,
        action_code,
        warmup_complete,
    )
    store.update(
        hour_id,
        tx_hash=chain_result["submit"].get("arbitrum"),
        tx_hash_rh=chain_result["submit"].get("robinhood"),
        rebalance_tx=chain_result["rebalance"].get("arbitrum"),
        rebalance_tx_rh=chain_result["rebalance"].get("robinhood"),
        pool_status_arb=chain_result["pool_status"].get("arbitrum"),
        pool_status_rh=chain_result["pool_status"].get("robinhood"),
    )
    row = store.get(hour_id)
    assert row is not None
    api = row_to_api(row, warmup_complete=warmup_complete)
    api["close"] = prediction.close
    log.info(
        "hour %s action=%s pred8h=$%.2f 1h=%+.2f 2h=%+.2f 8h=%+.2f bar=%s hash=%s",
        hour_id,
        action,
        prediction.pred_eth_usd_8h,
        prediction.eth_pct_1h.pct,
        prediction.eth_pct_2h.pct,
        prediction.eth_pct_8h.pct,
        prediction.bar_time,
        api["forecastHash"],
    )
    return api
