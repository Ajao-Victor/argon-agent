"""One UTC hour: infer → store → submit → rebalance."""

from __future__ import annotations

import logging
import time

from argon_agent.config import MODEL_ID_TEXT, WARMUP_SUBMITS
from argon_agent.db import Store, now_iso
from argon_agent.hashing import forecast_hash, hex_hash
from argon_agent.policy import action_name, allowed_action, catchup_pct, path_expected_price, pct_to_bps
from argon_agent.serialize import current_hour_id, row_to_api

log = logging.getLogger("argon.tick")

INFER_ATTEMPTS = 8
INFER_RETRY_SECS = 12


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
    prior_2h = store.get(hour_id - 2)
    expected_1h = None
    if prior_1h and prior_1h.get("pred_eth_usd_8h") and prior_1h.get("bar_close_usd"):
        from argon_agent.infer import HorizonPred

        start = float(prior_1h["bar_close_usd"])
        target = float(prior_1h["pred_eth_usd_8h"])
        expected_1h = path_expected_price(start, target, 1)
        prediction.eth_pct_1h = HorizonPred(
            pct=catchup_pct(start, target, prediction.close, 1),
            source="catchup",
        )
        log.info(
            "hour %s 1h catch-up vs prior $%.2f path: expected $%.2f actual $%.2f",
            hour_id,
            target,
            expected_1h,
            prediction.close,
        )
    if prior_2h and prior_2h.get("pred_eth_usd_8h") and prior_2h.get("bar_close_usd"):
        from argon_agent.infer import HorizonPred

        start = float(prior_2h["bar_close_usd"])
        target = float(prior_2h["pred_eth_usd_8h"])
        prediction.eth_pct_2h = HorizonPred(
            pct=catchup_pct(start, target, prediction.close, 2),
            source="catchup",
        )
    if (
        prior_1h
        and int(prior_1h["hour_id"]) != hour_id
        and float(prior_1h.get("eth_pct_8h") or 0) == prediction.eth_pct_8h.pct
        and abs(float(prior_1h.get("pred_eth_usd_8h") or 0) - prediction.pred_eth_usd_8h) < 1e-9
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
    warmup_complete = db_count + 1 >= WARMUP_SUBMITS
    if live:
        onchain = live[0].onchain_warmup_complete()
        if onchain is True:
            warmup_complete = True

    action_code = allowed_action(
        pct1h_bps, pct2h_bps, pct8h_bps, in_pool=in_pool, warmup_complete=warmup_complete
    )
    action = action_name(action_code, warmup_complete)

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
