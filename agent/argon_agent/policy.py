"""Dual-horizon gate. Must match contracts/src/libraries/DualHorizonGate.sol."""

from __future__ import annotations

import math

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


def pred_price_from_log_return(close: float, log_return: float) -> float:
    """8h model outputs log-return; this is ETH USD at the horizon from that bar close."""
    return float(close) * math.exp(float(log_return))


def pct_from_prices(start: float, predicted: float) -> float:
    return (float(predicted) / float(start) - 1.0) * 100.0


def path_expected_price(start: float, target: float, elapsed: int, horizon: int = 8) -> float:
    """Geometric path from the hour-0 close to the submitted 8h predicted price."""
    start = float(start)
    target = float(target)
    if start <= 0 or target <= 0 or horizon <= 0:
        raise ValueError("invalid price path")
    frac = min(max(int(elapsed), 0) / float(horizon), 1.0)
    return start * ((target / start) ** frac)


def catchup_pct(start: float, target: float, current: float, elapsed: int, horizon: int = 8) -> float:
    """% the current bar is off the previously submitted 8h price path (1h catch-up)."""
    expected = path_expected_price(start, target, elapsed, horizon)
    return (float(current) / expected - 1.0) * 100.0


def tripped_horizons(pct1h_bps: int, pct2h_bps: int, pct8h_bps: int) -> list[str]:
    out: list[str] = []
    if abs(pct1h_bps) >= GATE_1H_BPS:
        out.append("1h")
    if abs(pct2h_bps) >= GATE_2H_BPS:
        out.append("2h")
    if abs(pct8h_bps) >= GATE_8H_BPS:
        out.append("8h")
    return out
