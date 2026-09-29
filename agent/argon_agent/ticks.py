"""Uniswap v3 tick range around the current pool tick."""

from __future__ import annotations

from argon_agent.abis import FACTORY_ABI, NPM_ABI, POOL_ABI
from argon_agent.config import TICK_HALF_WIDTH

MIN_TICK = -887272
MAX_TICK = 887272
SPACING_BY_FEE = {100: 1, 500: 10, 3000: 60, 10000: 200}


def nearest_usable_tick(tick: int, spacing: int) -> int:
    rounded = int(round(tick / spacing) * spacing)
    if rounded < MIN_TICK:
        return (MIN_TICK // spacing) * spacing
    if rounded > MAX_TICK:
        return (MAX_TICK // spacing) * spacing
    return int(rounded)


def range_around(tick: int, spacing: int, half_width: int = TICK_HALF_WIDTH) -> tuple[int, int]:
    width = max(spacing, (half_width // spacing) * spacing)
    lower = nearest_usable_tick(tick - width, spacing)
    upper = nearest_usable_tick(tick + width, spacing)
    if lower >= upper:
        upper = lower + spacing
    return lower, upper


def current_range(w3, npm: str, token_a: str, token_b: str, fee: int) -> tuple[int, int]:
    from web3 import Web3

    npm_c = w3.eth.contract(address=Web3.to_checksum_address(npm), abi=NPM_ABI)
    factory_addr = npm_c.functions.factory().call()
    factory = w3.eth.contract(address=Web3.to_checksum_address(factory_addr), abi=FACTORY_ABI)
    token_a_c = Web3.to_checksum_address(token_a)
    token_b_c = Web3.to_checksum_address(token_b)
    pool_addr = factory.functions.getPool(token_a_c, token_b_c, fee).call()
    if int(pool_addr, 16) == 0:
        raise RuntimeError(f"no Uniswap pool for {token_a}/{token_b} fee {fee}")
    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_addr), abi=POOL_ABI)
    slot0 = pool.functions.slot0().call()
    tick = int(slot0[1])
    try:
        spacing = int(factory.functions.feeAmountTickSpacing(fee).call())
    except Exception:
        spacing = SPACING_BY_FEE.get(fee, 10)
    if spacing <= 0:
        spacing = 10
    return range_around(tick, spacing)
