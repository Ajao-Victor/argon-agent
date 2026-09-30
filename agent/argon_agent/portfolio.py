"""Read-only vault balances for the frontend. No keeper key required."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from web3 import Web3

from argon_agent.abis import ADAPTER_ABI, ERC20_ABI, ORACLE_ABI, VAULT_ABI
from argon_agent.accounting import pro_rata, usd8_from_stable, usd8_from_weth, usd8_to_float
from argon_agent.config import ADAPTER, VAULT, ChainCfg, chains

log = logging.getLogger("argon.portfolio")


def _w3(cfg: ChainCfg) -> Web3:
    return Web3(Web3.HTTPProvider(cfg.rpc, request_kwargs={"timeout": 20}))


def read_chain(cfg: ChainCfg, user: str | None) -> dict:
    w3 = _w3(cfg)
    if not w3.is_connected():
        raise RuntimeError(f"{cfg.name} RPC not connected")
    vault = w3.eth.contract(address=Web3.to_checksum_address(VAULT), abi=VAULT_ABI)
    weth = Web3.to_checksum_address(vault.functions.weth().call())
    stable = Web3.to_checksum_address(vault.functions.stable().call())
    decimals = int(vault.functions.stableDecimals().call())
    oracle_addr = Web3.to_checksum_address(vault.functions.oracle().call())
    oracle = w3.eth.contract(address=oracle_addr, abi=ORACLE_ABI)
    eth_usd8 = int(oracle.functions.ethUsd8().call())

    weth_c = w3.eth.contract(address=weth, abi=ERC20_ABI)
    stable_c = w3.eth.contract(address=stable, abi=ERC20_ABI)
    idle_weth = int(weth_c.functions.balanceOf(vault.address).call())
    idle_stable = int(stable_c.functions.balanceOf(vault.address).call())

    lp_weth = 0
    lp_stable = 0
    in_pool = False
    try:
        adapter_addr, _gated, exists, _ex, _rb = vault.functions.pools(cfg.pool_id).call()
        if exists and int(adapter_addr, 16) != 0:
            adapter = w3.eth.contract(address=Web3.to_checksum_address(adapter_addr), abi=ADAPTER_ABI)
            in_pool = bool(adapter.functions.inPosition().call())
            if in_pool:
                token_a = Web3.to_checksum_address(adapter.functions.tokenA().call())
                token_b = Web3.to_checksum_address(adapter.functions.tokenB().call())
                amt_a, amt_b = adapter.functions.amounts().call()
                for token, amt in ((token_a, int(amt_a)), (token_b, int(amt_b))):
                    if token.lower() == weth.lower():
                        lp_weth += amt
                    elif token.lower() == stable.lower():
                        lp_stable += amt
        else:
            adapter = w3.eth.contract(address=Web3.to_checksum_address(ADAPTER), abi=ADAPTER_ABI)
            in_pool = bool(adapter.functions.inPosition().call())
    except Exception:
        log.exception("pool/adapter read failed on %s", cfg.name)

    total_usd8 = usd8_from_weth(idle_weth + lp_weth, eth_usd8) + usd8_from_stable(
        idle_stable + lp_stable, decimals
    )
    supply = int(vault.functions.totalShares().call())
    shares = 0
    user_idle_weth = 0
    user_idle_stable = 0
    wallet_weth = 0
    wallet_stable = 0
    if user:
        user_c = Web3.to_checksum_address(user)
        shares = int(vault.functions.shareBalance(user_c).call())
        user_idle_weth = int(vault.functions.idleBalance(user_c, weth).call())
        user_idle_stable = int(vault.functions.idleBalance(user_c, stable).call())
        wallet_weth = int(weth_c.functions.balanceOf(user_c).call())
        wallet_stable = int(stable_c.functions.balanceOf(user_c).call())
    user_usd8 = pro_rata(total_usd8, shares, supply)
    pair = "WETH/USDC" if cfg.name == "arbitrum" else "WETH/USDG"
    return {
        "name": cfg.name,
        "chainId": cfg.chain_id,
        "vault": VAULT,
        "poolId": cfg.pool_id,
        "pair": pair,
        "inPool": in_pool,
        "ethUsd": eth_usd8 / 1e8,
        "totalShares": str(supply),
        "tvlUsd": usd8_to_float(total_usd8),
        "shares": str(shares),
        "shareUsd": usd8_to_float(user_usd8),
        "idleWeth": str(user_idle_weth),
        "idleStable": str(user_idle_stable),
        "idleWethFormatted": user_idle_weth / 1e18,
        "idleStableFormatted": user_idle_stable / 10**decimals,
        "walletWeth": str(wallet_weth),
        "walletStable": str(wallet_stable),
        "walletWethFormatted": wallet_weth / 1e18,
        "walletStableFormatted": wallet_stable / 10**decimals,
        "stableSymbol": "USDC" if cfg.name == "arbitrum" else "USDG",
        "stableDecimals": decimals,
    }


def snapshot(user: str | None = None) -> dict:
    chains_out: dict[str, dict] = {}
    total = 0.0
    for cfg in chains():
        if not cfg.enabled:
            continue
        try:
            row = read_chain(cfg, user)
            chains_out[cfg.name] = row
            total += float(row["shareUsd"])
        except Exception as exc:
            log.exception("portfolio read failed on %s", cfg.name)
            chains_out[cfg.name] = {"name": cfg.name, "chainId": cfg.chain_id, "error": str(exc)}
    return {
        "address": Web3.to_checksum_address(user) if user else None,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "pollSeconds": 10,
        "totalUsd": total,
        "chains": chains_out,
    }
