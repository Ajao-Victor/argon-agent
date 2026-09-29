"""On-chain forecast hash. Must match InferenceRegistry.computeHash."""

from __future__ import annotations

from eth_abi import encode
from eth_utils import keccak

from argon_agent.config import MODEL_ID_TEXT


def model_id_bytes(text: str = MODEL_ID_TEXT) -> bytes:
    return keccak(text.encode("utf-8"))


def forecast_hash(
    hour_id: int,
    pct1h_bps: int,
    pct2h_bps: int,
    pct8h_bps: int,
    model_id: bytes | None = None,
) -> bytes:
    mid = model_id if model_id is not None else model_id_bytes()
    packed = encode(
        ["uint64", "int256", "int256", "int256", "bytes32"],
        [int(hour_id), int(pct1h_bps), int(pct2h_bps), int(pct8h_bps), mid],
    )
    return keccak(packed)


def hex_hash(value: bytes) -> str:
    return "0x" + value.hex()
