"""Dual-horizon gate. Must match contracts/src/libraries/DualHorizonGate.sol."""

from __future__ import annotations

from argon_agent.config import GATE_1H_BPS, GATE_2H_BPS, GATE_8H_BPS

HOLD = 0
ENTER = 1
EXIT = 2

ACTION_NAME = {HOLD: "hold", ENTER: "enter", EXIT: "exit"}


def pct_to_bps(pct: float) -> int:
    """-2.41% → -241. Same encoding the registry stores."""
    return int(round(float(pct) * 100.0))


def bps_to_pct(bps: int) -> float:
    return float(bps) / 100.0


def allowed_action(
    pct1h_bps: int,
    pct2h_bps: int,
    pct8h_bps: int,
    *,
    in_pool: bool,
    gate1h: int = GATE_1H_BPS,
    gate2h: int = GATE_2H_BPS,
    gate8h: int = GATE_8H_BPS,
    warmup_complete: bool = True,
) -> int:
    if not warmup_complete:
        return HOLD
    must_exit = abs(pct1h_bps) >= gate1h or abs(pct2h_bps) >= gate2h
    if must_exit:
        return EXIT
    if in_pool:
        return HOLD
    can_enter = abs(pct1h_bps) < gate1h and abs(pct2h_bps) < gate2h and abs(pct8h_bps) < gate8h
    return ENTER if can_enter else EXIT


def action_name(code: int, warmup_complete: bool) -> str:
    if not warmup_complete:
        return "warmup"
    return ACTION_NAME[code]


def tripped_horizons(pct1h_bps: int, pct2h_bps: int, pct8h_bps: int) -> list[str]:
    out: list[str] = []
    if abs(pct1h_bps) >= GATE_1H_BPS:
        out.append("1h")
    if abs(pct2h_bps) >= GATE_2H_BPS:
        out.append("2h")
    if abs(pct8h_bps) >= GATE_8H_BPS:
        out.append("8h")
    return out
