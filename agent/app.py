"""Read-only FastAPI surface for the Vercel frontend."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware

from argon_agent.config import (
    GATE_1H_BPS,
    GATE_2H_BPS,
    GATE_8H_BPS,
    MODEL_ID_TEXT,
    WARMUP_SUBMITS,
    eight_h_loaded,
    frontend_origins,
)
from argon_agent.db import Store, now_iso
from argon_agent.pools import list_pools, warm_apr_cache
from argon_agent.portfolio import snapshot
from argon_agent.policy import gate_message, resolve_gate
from argon_agent.serialize import current_hour_id, row_to_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("argon.api")

store = Store()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.ensure_schema()
    warm_apr_cache()
    log.info("schema ready postgres=%s model8h=%s", store.postgres, eight_h_loaded())
    yield


app = FastAPI(title="Argon agent", version="0.1.0", lifespan=lifespan)

origins = frontend_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins != ["*"] else ["*"],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def _warmup_state() -> tuple[bool, int, int | None, int]:
    db_n = 0
    try:
        db_n = store.count()
    except Exception:
        pass
    onchain_n = None
    try:
        from argon_agent.chain import registry_forecast_count

        onchain_n = registry_forecast_count()
    except Exception:
        log.exception("on-chain forecastCount failed")
    # The progress bar counts stored inferences. onchainForecastCount stays 0 while DRY_RUN.
    remaining = 0 if db_n >= WARMUP_SUBMITS else max(0, WARMUP_SUBMITS - db_n)
    chain_complete = (onchain_n >= WARMUP_SUBMITS) if onchain_n is not None else db_n >= WARMUP_SUBMITS
    return chain_complete, remaining, onchain_n, db_n


def _warmup() -> bool:
    complete, _, _, _ = _warmup_state()
    return complete


@app.get("/")
def root():
    payload = status()
    payload["endpoints"] = {
        "health": "/health",
        "status": "/status",
        "latestForecast": "/forecasts/latest",
        "forecasts": "/forecasts?limit=24",
        "vault": "/vault",
        "pools": "/pools",
        "portfolio": "/portfolio/0xYourAddress",
    }
    if payload.get("lastHourId") is None:
        payload["hint"] = (
            "No forecasts yet. Add Heroku Postgres, turn the clock dyno on, "
            "then wait for the next UTC hour (or restart the clock process)."
        )
    return payload


@app.get("/health")
def health():
    return {
        "ok": True,
        "modelLoaded": eight_h_loaded(),
        "database": "postgres" if store.postgres else "sqlite",
    }


@app.get("/status")
def status():
    warmup, remaining, onchain_n, db_n = _warmup_state()
    latest = None
    try:
        latest = store.latest()
    except Exception:
        log.exception("status latest failed")
    last_hour = int(latest["hour_id"]) if latest else None
    current = current_hour_id()
    payload = {
        "ok": True,
        "warmupComplete": warmup,
        "hoursUntilFirstDecision": remaining,
        "gate1hBps": GATE_1H_BPS,
        "gate2hBps": GATE_2H_BPS,
        "gate8hBps": GATE_8H_BPS,
        "lastHourId": last_hour,
        "currentHourId": current,
        "liveForecast": last_hour == current,
        "modelId": MODEL_ID_TEXT,
        "modelLoaded": eight_h_loaded(),
        "dryRun": os.getenv("DRY_RUN", "false"),
        "database": "postgres" if store.postgres else "sqlite",
        "onchainForecastCount": onchain_n,
        "dbForecastCount": db_n,
    }
    if last_hour != current:
        payload["hint"] = (
            "No live infer for this UTC hour yet. Do not display lastHourId as the current forecast."
        )
    return payload


@app.get("/forecasts/latest")
def latest_forecast():
    current = current_hour_id()
    row = store.get(current) or store.latest()
    if not row:
        raise HTTPException(
            status_code=404,
            detail={
                "reason": "no forecasts stored yet",
                "currentHourId": current,
            },
        )
    return row_to_api(row, warmup_complete=_warmup(), now_hour=current)


@app.get("/forecasts")
def list_forecasts(limit: int = Query(24, ge=1, le=168)):
    items = [row_to_api(r, warmup_complete=_warmup()) for r in store.list_recent(limit)]
    return {"items": items}


@app.get("/pools")
def pools():
    """Arb vs Robinhood ETH LP cards: Uniswap TVL + APR. Pick one chain before deposit."""
    return list_pools()


@app.get("/vault")
def vault_snapshot():
    """TVL and pool status on both chains. No wallet required."""
    return snapshot(None)


@app.get("/portfolio/{address}")
def user_portfolio(address: str):
    """Per-wallet vault equity in USD. Poll every 10s for near-real-time UI."""
    from web3 import Web3

    if not Web3.is_address(address):
        raise HTTPException(status_code=400, detail="invalid address")
    payload = snapshot(address)
    current = current_hour_id()
    live = store.get(current) or store.latest()
    payload["forecast"] = row_to_api(live, warmup_complete=_warmup(), now_hour=current) if live else None
    return payload


@app.get("/forecasts/{hour_id}")
def get_forecast(hour_id: int):
    row = store.get(hour_id)
    if not row:
        raise HTTPException(status_code=404, detail="unknown hourId")
    return row_to_api(row, warmup_complete=_warmup())


class GateRequest(BaseModel):
    address: str
    preset: str
    topBps: int = Field(default=100)
    bottomBps: int = Field(default=-100)
    issuedAt: int
    signature: str


def _gate_public(row: dict) -> dict:
    return {
        "address": row["address"],
        "preset": row["preset"],
        "top1hBps": int(row["top_1h_bps"]),
        "bottom1hBps": int(row["bottom_1h_bps"]),
        "top2hBps": int(row["top_2h_bps"]),
        "bottom2hBps": int(row["bottom_2h_bps"]),
        "top8hBps": int(row["top_8h_bps"]),
        "bottom8hBps": int(row["bottom_8h_bps"]),
        "inPosition": bool(row.get("in_position")),
        "lastAction": row.get("last_action"),
        "lastHourId": row.get("last_hour_id"),
        "issuedAt": int(row["issued_at"]),
    }


@app.get("/gates/{address}")
def get_gate(address: str):
    from web3 import Web3

    if not Web3.is_address(address):
        raise HTTPException(status_code=400, detail="invalid address")
    row = store.get_gate(Web3.to_checksum_address(address))
    if not row:
        raise HTTPException(status_code=404, detail="no gate stored for this signer")
    return _gate_public(row)


@app.post("/gates")
def set_gate(body: GateRequest):
    from datetime import datetime, timezone

    from eth_account import Account
    from eth_account.messages import encode_defunct
    from web3 import Web3

    if not Web3.is_address(body.address):
        raise HTTPException(status_code=400, detail="invalid address")
    address = Web3.to_checksum_address(body.address)
    try:
        resolved = resolve_gate(body.preset, body.topBps, body.bottomBps)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if resolved["preset"] != "custom" and (
        body.topBps != resolved["top_1h_bps"] or body.bottomBps != resolved["bottom_1h_bps"]
    ):
        raise HTTPException(status_code=400, detail="preset does not match the signed gate values")
    now = int(datetime.now(timezone.utc).timestamp())
    if abs(now - int(body.issuedAt)) > 2 * 3600:
        raise HTTPException(status_code=400, detail="gate signature is outside the 2 hour window")
    message = gate_message(address, resolved["preset"], body.topBps, body.bottomBps, body.issuedAt)
    try:
        recovered = Account.recover_message(encode_defunct(text=message), signature=body.signature)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid signature") from exc
    if recovered.lower() != address.lower():
        raise HTTPException(status_code=400, detail="signature is not from this signer")
    existing = store.get_gate(address)
    if existing and int(body.issuedAt) <= int(existing["issued_at"]):
        raise HTTPException(status_code=409, detail="a newer gate is already stored for this signer")
    store.save_gate(
        {
            "address": address,
            "preset": resolved["preset"],
            "top_1h_bps": resolved["top_1h_bps"],
            "bottom_1h_bps": resolved["bottom_1h_bps"],
            "top_2h_bps": resolved["top_2h_bps"],
            "bottom_2h_bps": resolved["bottom_2h_bps"],
            "top_8h_bps": resolved["top_8h_bps"],
            "bottom_8h_bps": resolved["bottom_8h_bps"],
            "signature": body.signature,
            "signed_message": message,
            "issued_at": int(body.issuedAt),
            "updated_at": now_iso(),
            "in_position": int(existing["in_position"]) if existing else 0,
            "last_action": existing.get("last_action") if existing else None,
            "last_hour_id": existing.get("last_hour_id") if existing else None,
        }
    )
    saved = store.get_gate(address)
    assert saved is not None
    return _gate_public(saved)
