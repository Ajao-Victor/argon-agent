"""Share and USD-8 math used by the vault accounting."""


def usd8_from_weth(amount: int, eth_usd8: int) -> int:
    return int(amount) * int(eth_usd8) // 10**18


def usd8_from_stable(amount: int, decimals: int) -> int:
    return int(amount) * 10**8 // 10**int(decimals)


def pro_rata(total: int, shares: int, supply: int) -> int:
    if supply <= 0 or shares <= 0:
        return 0
    return int(total) * int(shares) // int(supply)


def usd8_to_float(usd8: int) -> float:
    return int(usd8) / 1e8


def fee_apr_pct(volume_usd_1d: float, tvl_usd: float, fee_ppm: int) -> float | None:
    """Uniswap v3 LP fee APR from 24h volume. fee_ppm is the pool fee (500 = 0.05%)."""
    if tvl_usd <= 0 or volume_usd_1d < 0 or fee_ppm <= 0:
        return None
    return float(volume_usd_1d) * (float(fee_ppm) / 1_000_000.0) / float(tvl_usd) * 365.0 * 100.0


def _signed_word(word: bytes) -> int:
    value = int.from_bytes(word, "big")
    if value >= 2**255:
        value -= 2**256
    return value


def swap_volume_usd(
    data: bytes,
    *,
    token0: str,
    weth: str,
    stable: str,
    stable_decimals: int,
    eth_usd8: int,
) -> float:
    """USD notional of one Uniswap v3 Swap. Uses one side so volume is not doubled."""
    if len(data) < 64:
        return 0.0
    amount0 = abs(_signed_word(data[0:32]))
    t0 = token0.lower()
    if t0 == weth.lower():
        usd8 = usd8_from_weth(amount0, eth_usd8)
    elif t0 == stable.lower():
        usd8 = usd8_from_stable(amount0, stable_decimals)
    else:
        amount1 = abs(_signed_word(data[32:64]))
        usd8 = usd8_from_stable(amount1, stable_decimals)
    return usd8_to_float(usd8)
