from argon_agent.accounting import fee_apr_pct, pro_rata, usd8_from_stable, usd8_from_weth, usd8_to_float


def test_usd8_weth():
    # 1 WETH at $2688.25
    eth_usd8 = 268825000000
    assert usd8_from_weth(10**18, eth_usd8) == eth_usd8
    assert abs(usd8_to_float(eth_usd8) - 2688.25) < 1e-9


def test_usd8_usdc():
    # 100 USDC (6 decimals) → $100
    assert usd8_from_stable(100 * 10**6, 6) == 100 * 10**8


def test_pro_rata():
    assert pro_rata(1_000 * 10**8, 25, 100) == 250 * 10**8
    assert pro_rata(100, 0, 10) == 0
    assert pro_rata(100, 1, 0) == 0


def test_uniswap_fee_apr_from_daily_volume():
    # $1m volume, $1m TVL, 0.05% fee → 0.05% * 365 = 18.25% APR
    apr = fee_apr_pct(1_000_000, 1_000_000, 500)
    assert apr is not None
    assert abs(apr - 18.25) < 1e-9
    assert fee_apr_pct(100, 0, 500) is None
