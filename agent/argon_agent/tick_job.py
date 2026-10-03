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


def _news_windows(store: Store, *, refresh: bool) -> list:
    """Never lets a calendar problem stop the hour: falls back to the built-in official dates."""
    from argon_agent import news

    try:
        return news.windows(store, refresh=refresh)
    except Exception:
        log.exception("news calendar failed; using official dates only")
        return news.windows(None, refresh=False)


def _apply_signer_gates(
    store: Store,
    hour_id: int,
    pct1h_bps: int,
    pct2h_bps: int,
    pct8h_bps: int,
    warmup_complete: bool,
    news_paused: bool = False,
) -> None:
    rows = []
    for gate in store.list_gates():
        action, in_position = user_action(
            pct1h_bps,
            pct2h_bps,
            pct8h_bps,
            gate,
            in_position=bool(gate.get("in_position")),
            warmup_complete=warmup_complete,
            news_paused=news_paused,
        )
        rows.append((action, hour_id, 1 if in_position else 0, str(gate["address"])))
        log.info("signer %s hour %s action=%s in=%s", gate["address"], hour_id, action, in_position)
    store.save_gate_decisions(rows)


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


def _chain_incomplete(row: dict) -> bool:
    import os

    if os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes"):
        return False
    if row.get("action") == "warmup":
        return False
    for key in ("tx_hash", "tx_hash_rh", "rebalance_tx", "rebalance_tx_rh"):
        val = row.get(key)
        if isinstance(val, str) and val.startswith("error:"):
            return True
    return False


_RETRIES: dict[int, int] = {}
MAX_CHAIN_RETRIES = 3


def _retry_chain(store: Store, row: dict) -> dict:
    from argon_agent import chain as chainmod
    from argon_agent.hashing import forecast_hash as fh
    from argon_agent.policy import pct_to_bps as tb

    hour_id = int(row["hour_id"])
    _RETRIES[hour_id] = _RETRIES.get(hour_id, 0) + 1
    if _RETRIES[hour_id] > MAX_CHAIN_RETRIES:
        log.error("hour %s keeper legs failed %s times; giving up until next hour", hour_id, MAX_CHAIN_RETRIES)
        return row_to_api(row, warmup_complete=True)
    p1, p2, p8 = tb(row["eth_pct_1h"]), tb(row["eth_pct_2h"]), tb(row["eth_pct_8h"])
    live = chainmod.clients()
    wins = _news_windows(store, refresh=False)
    res = chainmod.execute_hour(live, hour_id, p1, p2, p8, fh(hour_id, p1, p2, p8), 0, True, wins)
    store.update(
        hour_id,
        tx_hash=res["submit"].get("arbitrum") or row.get("tx_hash"),
        tx_hash_rh=res["submit"].get("robinhood") or row.get("tx_hash_rh"),
        rebalance_tx=res["rebalance"].get("arbitrum"),
        rebalance_tx_rh=res["rebalance"].get("robinhood"),
    )
    fresh = store.get(hour_id) or row
    if _chain_incomplete(fresh):
        raise RuntimeError(f"hour {hour_id} keeper legs still failing; clock will retry in 60s")
    return row_to_api(fresh, warmup_complete=True)


def run_hour(store: Store | None = None) -> dict:
    from argon_agent import chain as chainmod

    store = store or Store()
    store.ensure_schema()
    hour_id = current_hour_id()
    existing = store.get(hour_id)
    if existing and not _chain_incomplete(existing):
        log.info("hour %s already stored (live)", hour_id)
        warmup = store.count() >= WARMUP_SUBMITS
        return row_to_api(existing, warmup_complete=warmup)
    if existing:
        # [FIX M-missed-exit] the forecast is stored but a chain leg failed: retry the keeper legs only.
        return _retry_chain(store, existing)

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

    from argon_agent import news

    wins = _news_windows(store, refresh=True)
    pause = news.active_window(wins, hour_id)
    if pause is not None:
        log.info(
            "hour %s news pause [%s,%s) for %s",
            hour_id,
            pause.from_hour,
            pause.until_hour,
            ", ".join(e.title for e in pause.events),
        )
    action_code = allowed_action(
        pct1h_bps,
        pct2h_bps,
        pct8h_bps,
        in_pool=in_pool,
        warmup_complete=warmup_complete,
        news_paused=pause is not None,
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
        wins,
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
    try:
        _apply_signer_gates(
            store, hour_id, pct1h_bps, pct2h_bps, pct8h_bps, warmup_complete, news_paused=pause is not None
        )
    except Exception:
        log.exception("signer gate update failed after keeper legs")
    row = store.get(hour_id)
    assert row is not None
    if _chain_incomplete(row):
        raise RuntimeError(f"hour {hour_id} keeper leg failed; clock retries this hour in 60s")
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
