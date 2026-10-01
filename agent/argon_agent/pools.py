"""Uniswap v3 ETH-stable pool cards: TVL + APR for Arb vs Robinhood."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests
from web3 import Web3

from argon_agent.abis import ERC20_ABI, FACTORY_ABI, NPM_ABI, ORACLE_ABI, POOL_ABI, VAULT_ABI
from argon_agent.accounting import fee_apr_pct, usd8_from_stable, usd8_from_weth, usd8_to_float
from argon_agent.config import VAULT, ChainCfg, chains

log = logging.getLogger("argon.pools")

LLAMA_URL = "https://yields.llama.fi/pools"
DEX_PAIR_URL = "https://api.dexscreener.com/latest/dex/pairs/{chain}/{pool}"
_LLAMA_CACHE: dict = {"ts": 0.0, "rows": []}
_LLAMA_TTL = 300.0
_DEX_CACHE: dict[str, tuple[float, dict | None]] = {}
_DEX_TTL = 300.0
_CHAIN_LLAMA = {"arbitrum": "Arbitrum", "robinhood": "Robinhood"}
_CHAIN_DEX = {"arbitrum": "arbitrum", "robinhood": "robinhood"}


def _w3(cfg: ChainCfg) -> Web3:
    return Web3(Web3.HTTPProvider(cfg.rpc, request_kwargs={"timeout": 20}))


def pool_address(w3: Web3, cfg: ChainCfg) -> str:
    npm = w3.eth.contract(address=Web3.to_checksum_address(cfg.npm), abi=NPM_ABI)
    factory = w3.eth.contract(address=Web3.to_checksum_address(npm.functions.factory().call()), abi=FACTORY_ABI)
    addr = factory.functions.getPool(
        Web3.to_checksum_address(cfg.token_a),
        Web3.to_checksum_address(cfg.token_b),
        cfg.fee,
    ).call()
    if int(addr, 16) == 0:
        raise RuntimeError(f"no Uniswap v3 pool on {cfg.name}")
    return Web3.to_checksum_address(addr)


def llama_rows() -> list[dict]:
    now = time.time()
    if _LLAMA_CACHE["rows"] and now - _LLAMA_CACHE["ts"] < _LLAMA_TTL:
        return _LLAMA_CACHE["rows"]
    try:
        resp = requests.get(LLAMA_URL, timeout=20)
        resp.raise_for_status()
        rows = resp.json().get("data") or []
        _LLAMA_CACHE["ts"] = now
        _LLAMA_CACHE["rows"] = rows
        return rows
    except Exception:
        log.exception("DefiLlama yields fetch failed")
        return _LLAMA_CACHE["rows"]


def match_llama_apr(cfg: ChainCfg, pool: str) -> dict:
    want_chain = _CHAIN_LLAMA.get(cfg.name, cfg.name)
    a = cfg.token_a.lower()
    b = cfg.token_b.lower()
    pool_l = pool.lower()
    best = None
    for row in llama_rows():
        if str(row.get("chain") or "") != want_chain:
            continue
        project = str(row.get("project") or "")
        if "uniswap" not in project:
            continue
        tokens = [str(t).lower() for t in (row.get("underlyingTokens") or [])]
        meta = str(row.get("poolMeta") or "").lower()
        pool_id = str(row.get("pool") or "").lower()
        addr_hit = pool_l in pool_id or pool_id.endswith(pool_l[2:])
        token_hit = a in tokens and b in tokens
        fee_hit = "0.05" in meta or meta in ("500", "0.05%")
        if addr_hit or (token_hit and (fee_hit or not meta)):
            apy = row.get("apy")
            if apy is None:
                continue
            cand = {
                "aprPct": float(apy),
                "aprBasePct": float(row["apyBase"]) if row.get("apyBase") is not None else None,
                "aprSource": "defillama",
                "llamaTvlUsd": float(row["tvlUsd"]) if row.get("tvlUsd") is not None else None,
                "volumeUsd1d": float(row["volumeUsd1d"]) if row.get("volumeUsd1d") is not None else None,
            }
            if addr_hit:
                return cand
            if best is None or (cand["llamaTvlUsd"] or 0) > (best["llamaTvlUsd"] or 0):
                best = cand
    return best or {"aprPct": None, "aprBasePct": None, "aprSource": "unavailable", "llamaTvlUsd": None, "volumeUsd1d": None}


def dex_pair(cfg: ChainCfg, pool: str) -> dict | None:
    chain = _CHAIN_DEX.get(cfg.name)
    if not chain:
        return None
    key = f"{chain}:{pool.lower()}"
    now = time.time()
    packed = _DEX_CACHE.get(key)
    if packed and now - packed[0] < _DEX_TTL:
        return packed[1]
    try:
        resp = requests.get(DEX_PAIR_URL.format(chain=chain, pool=pool), timeout=15)
        resp.raise_for_status()
        pairs = resp.json().get("pairs") or []
        hit = None
        for p in pairs:
            if str(p.get("pairAddress") or "").lower() == pool.lower():
                hit = p
                break
        _DEX_CACHE[key] = (now, hit)
        return hit
    except Exception:
        log.exception("DexScreener fetch failed for %s %s", cfg.name, pool)
        return packed[1] if packed else None


def match_apr(cfg: ChainCfg, pool: str, tvl_usd: float | None = None) -> dict:
    llama = match_llama_apr(cfg, pool)
    if llama.get("aprPct") is not None:
        return llama
    pair = dex_pair(cfg, pool)
    vol = None
    dex_tvl = None
    if pair:
        vol = (pair.get("volume") or {}).get("h24")
        dex_tvl = (pair.get("liquidity") or {}).get("usd")
    tvl = tvl_usd if tvl_usd and tvl_usd > 0 else dex_tvl
    if vol is not None and tvl:
        apr = fee_apr_pct(float(vol), float(tvl), cfg.fee)
        if apr is not None:
            return {
                "aprPct": apr,
                "aprBasePct": apr,
                "aprSource": "dexscreener",
                "llamaTvlUsd": llama.get("llamaTvlUsd"),
                "volumeUsd1d": float(vol),
            }
    return {
        "aprPct": None,
        "aprBasePct": None,
        "aprSource": "unavailable",
        "llamaTvlUsd": llama.get("llamaTvlUsd"),
        "volumeUsd1d": float(vol) if vol is not None else None,
    }


def describe_pool(cfg: ChainCfg) -> dict:
    pair = "WETH/USDC" if cfg.name == "arbitrum" else "WETH/USDG"
    stable_symbol = "USDC" if cfg.name == "arbitrum" else "USDG"
    w3 = _w3(cfg)
    if not w3.is_connected():
        raise RuntimeError(f"{cfg.name} RPC not connected")
    vault = w3.eth.contract(address=Web3.to_checksum_address(VAULT), abi=VAULT_ABI)
    weth = Web3.to_checksum_address(vault.functions.weth().call())
    stable = Web3.to_checksum_address(vault.functions.stable().call())
    decimals = int(vault.functions.stableDecimals().call())
    oracle = w3.eth.contract(address=Web3.to_checksum_address(vault.functions.oracle().call()), abi=ORACLE_ABI)
    eth_usd8 = int(oracle.functions.ethUsd8().call())
    pool_addr = pool_address(w3, cfg)
    pool = w3.eth.contract(address=pool_addr, abi=POOL_ABI)
    token0 = Web3.to_checksum_address(pool.functions.token0().call())
    token1 = Web3.to_checksum_address(pool.functions.token1().call())
    bal0 = int(w3.eth.contract(address=token0, abi=ERC20_ABI).functions.balanceOf(pool_addr).call())
    bal1 = int(w3.eth.contract(address=token1, abi=ERC20_ABI).functions.balanceOf(pool_addr).call())
    tvl8 = 0
    for token, amt in ((token0, bal0), (token1, bal1)):
        if token.lower() == weth.lower():
            tvl8 += usd8_from_weth(amt, eth_usd8)
        elif token.lower() == stable.lower():
            tvl8 += usd8_from_stable(amt, decimals)
    in_pool = False
    try:
        in_pool = int(vault.functions.poolStatus(cfg.pool_id).call()) == 1
    except Exception:
        pass
    apr = match_apr(cfg, pool_addr, usd8_to_float(tvl8))
    return {
        "id": cfg.name,
        "chainId": cfg.chain_id,
        "poolId": cfg.pool_id,
        "pair": pair,
        "feePercent": cfg.fee / 10_000.0,
        "uniswapFee": cfg.fee,
        "vault": VAULT,
        "pool": pool_addr,
        "weth": weth,
        "stable": stable,
        "stableSymbol": stable_symbol,
        "inPool": in_pool,
        "poolTvlUsd": usd8_to_float(tvl8),
        "ethUsd": eth_usd8 / 1e8,
        "selectable": True,
        "depositHint": f"Switch wallet to {cfg.name} then deposit WETH + {stable_symbol}",
        **apr,
    }


def list_pools() -> dict:
    items = []
    for cfg in chains():
        if not cfg.enabled:
            continue
        try:
            items.append(describe_pool(cfg))
        except Exception as exc:
            log.exception("pool describe failed on %s", cfg.name)
            items.append(
                {
                    "id": cfg.name,
                    "chainId": cfg.chain_id,
                    "poolId": cfg.pool_id,
                    "error": str(exc),
                    "selectable": False,
                    "aprPct": None,
                    "aprSource": "unavailable",
                }
            )
    return {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "pollSeconds": 60,
        "selectOneChain": True,
        "pools": items,
    }
