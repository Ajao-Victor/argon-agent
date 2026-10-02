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


def implied_slice_pct(target: float, current: float, remaining_hours: int, horizon: int) -> float:
    """Percent move over `horizon` hours if the stored 8h price is reached in `remaining_hours`."""
    if remaining_hours < horizon or horizon <= 0 or current <= 0 or target <= 0:
        raise ValueError("forecast does not cover this horizon")
    return ((float(target) / float(current)) ** (horizon / float(remaining_hours)) - 1.0) * 100.0


def average_open_slice(calls: list[tuple[float, int]], current: float, horizon: int) -> float | None:
    """Average 1h or 2h slice of every 8h target that still covers that horizon."""
    values: list[float] = []
    for target, remaining in calls:
        if remaining < horizon:
            continue
        try:
            values.append(implied_slice_pct(target, current, remaining, horizon))
        except ValueError:
            continue
    if not values:
        return None
    return sum(values) / len(values)


PRESETS = {
    "safe": {"top_1h": 60, "top_2h": 120, "top_8h": 100},
    "balanced": {"top_1h": 100, "top_2h": 250, "top_8h": 200},
    "aggressive": {"top_1h": 200, "top_2h": 400, "top_8h": 350},
}


def resolve_gate(preset: str, top_bps: int, bottom_bps: int) -> dict[str, int]:
    name = preset.strip().lower()
    if name in PRESETS:
        band = PRESETS[name]
        return {
            "preset": name,
            "top_1h_bps": band["top_1h"],
            "bottom_1h_bps": -band["top_1h"],
            "top_2h_bps": band["top_2h"],
            "bottom_2h_bps": -band["top_2h"],
            "top_8h_bps": band["top_8h"],
            "bottom_8h_bps": -band["top_8h"],
        }
    if name != "custom":
        raise ValueError("unknown preset")
    if top_bps <= 0 or top_bps > 2000 or bottom_bps >= 0 or bottom_bps < -2000:
        raise ValueError("custom gate must be within ±20% and bottom must be negative")
    base = PRESETS["balanced"]
    return {
        "preset": "custom",
        "top_1h_bps": int(top_bps),
        "bottom_1h_bps": int(bottom_bps),
        "top_2h_bps": base["top_2h"],
        "bottom_2h_bps": -base["top_2h"],
        "top_8h_bps": base["top_8h"],
        "bottom_8h_bps": -base["top_8h"],
    }


def gate_message(address: str, preset: str, top_bps: int, bottom_bps: int, issued_at: int) -> str:
    return f"argon-gate:{address.lower()}:{preset.strip().lower()}:{int(top_bps)}:{int(bottom_bps)}:{int(issued_at)}"


def outside_band(pct_bps: int, top_bps: int, bottom_bps: int) -> bool:
    return pct_bps > top_bps or pct_bps < bottom_bps


def user_action(
    pct1h_bps: int,
    pct2h_bps: int,
    pct8h_bps: int,
    gate: dict,
    *,
    in_position: bool,
    warmup_complete: bool,
) -> tuple[str, bool]:
    """Per-signer action from that wallet's bands. Returns action and whether their capital stays in."""
    if not warmup_complete:
        return "warmup", bool(in_position)
    breach = outside_band(pct1h_bps, gate["top_1h_bps"], gate["bottom_1h_bps"]) or outside_band(
        pct2h_bps, gate["top_2h_bps"], gate["bottom_2h_bps"]
    )
    if breach:
        return "exit", False
    if in_position:
        return "hold", True
    can_enter = not outside_band(pct8h_bps, gate["top_8h_bps"], gate["bottom_8h_bps"])
    if can_enter:
        return "enter", True
    return "exit", False


def tripped_horizons(pct1h_bps: int, pct2h_bps: int, pct8h_bps: int) -> list[str]:
    out: list[str] = []
    if abs(pct1h_bps) >= GATE_1H_BPS:
        out.append("1h")
    if abs(pct2h_bps) >= GATE_2H_BPS:
        out.append("2h")
    if abs(pct8h_bps) >= GATE_8H_BPS:
        out.append("8h")
    return out
