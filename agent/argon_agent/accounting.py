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
