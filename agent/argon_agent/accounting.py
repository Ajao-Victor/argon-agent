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
