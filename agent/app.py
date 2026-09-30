"""Read-only FastAPI surface for the Vercel frontend."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
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
from argon_agent.db import Store
from argon_agent.portfolio import snapshot
from argon_agent.serialize import current_hour_id, row_to_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("argon.api")

store = Store()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.ensure_schema()
    log.info("schema ready postgres=%s model8h=%s", store.postgres, eight_h_loaded())
    yield


app = FastAPI(title="Argon agent", version="0.1.0", lifespan=lifespan)

origins = frontend_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins != ["*"] else ["*"],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _warmup() -> bool:
    try:
        return store.count() >= WARMUP_SUBMITS
    except Exception:
        return False


@app.get("/")
def root():
    payload = status()
    payload["endpoints"] = {
        "health": "/health",
        "status": "/status",
        "latestForecast": "/forecasts/latest",
        "forecasts": "/forecasts?limit=24",
        "vault": "/vault",
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
    warmup = _warmup()
    latest = None
    try:
        latest = store.latest()
    except Exception:
        log.exception("status latest failed")
    last_hour = int(latest["hour_id"]) if latest else None
    remaining = 0 if warmup else max(0, WARMUP_SUBMITS - store.count())
    return {
        "ok": True,
        "warmupComplete": warmup,
        "hoursUntilFirstDecision": remaining,
        "gate1hBps": GATE_1H_BPS,
        "gate2hBps": GATE_2H_BPS,
        "gate8hBps": GATE_8H_BPS,
        "lastHourId": last_hour,
        "currentHourId": current_hour_id(),
        "modelId": MODEL_ID_TEXT,
        "modelLoaded": eight_h_loaded(),
        "dryRun": os.getenv("DRY_RUN", "false"),
        "database": "postgres" if store.postgres else "sqlite",
    }


@app.get("/forecasts/latest")
def latest_forecast():
    row = store.latest()
    if not row:
        raise HTTPException(
            status_code=404,
            detail="no forecasts yet — add Postgres, enable the clock dyno, then wait for a tick",
        )
    return row_to_api(row, warmup_complete=_warmup())


@app.get("/forecasts")
def list_forecasts(limit: int = Query(24, ge=1, le=168)):
    items = [row_to_api(r, warmup_complete=_warmup()) for r in store.list_recent(limit)]
    return {"items": items}


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
    latest = store.latest()
    payload["forecast"] = row_to_api(latest, warmup_complete=_warmup()) if latest else None
    return payload


@app.get("/forecasts/{hour_id}")
def get_forecast(hour_id: int):
    row = store.get(hour_id)
    if not row:
        raise HTTPException(status_code=404, detail="unknown hourId")
    return row_to_api(row, warmup_complete=_warmup())
