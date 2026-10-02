"""Uniswap v3 ETH-stable pool cards: TVL + APR for Arb vs Robinhood."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import requests
from web3 import Web3

from argon_agent.abis import ERC20_ABI, FACTORY_ABI, NPM_ABI, POOL_ABI, VAULT_ABI
from argon_agent.accounting import fee_apr_pct, swap_volume_usd, usd8_from_stable, usd8_from_weth, usd8_to_float
from argon_agent.config import VAULT, ChainCfg, chains

log = logging.getLogger("argon.pools")

LLAMA_URL = "https://yields.llama.fi/pools"
COINBASE_TICKER = "https://api.exchange.coinbase.com/products/ETH-USD/ticker"
_LLAMA_CACHE: dict = {"ts": 0.0, "rows": []}
_LLAMA_TTL = 600.0
_LLAMA_LOCK = threading.Lock()
_SNAPSHOT: dict = {"ts": 0.0, "body": None}
_SNAPSHOT_TTL = 15.0
_CHAIN_LLAMA = {"arbitrum": "Arbitrum", "robinhood": "Robinhood"}
_EMPTY_APR = {
    "aprPct": None,
    "aprBasePct": None,
    "aprSource": "unavailable",
    "llamaTvlUsd": None,
    "volumeUsd1d": None,
}


def _w3(cfg: ChainCfg) -> Web3:
    return Web3(Web3.HTTPProvider(cfg.rpc, request_kwargs={"timeout": 6}))


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


def _refresh_llama() -> None:
    try:
        resp = requests.get(LLAMA_URL, timeout=25)
        resp.raise_for_status()
        rows = resp.json().get("data") or []
        with _LLAMA_LOCK:
            _LLAMA_CACHE["ts"] = time.time()
            _LLAMA_CACHE["rows"] = rows
        log.info("DefiLlama APR cache refreshed (%s rows)", len(rows))
    except Exception:
        log.exception("DefiLlama yields fetch failed")


def warm_apr_cache() -> None:
    """Refresh APR off the request path so /pools is not blocked on the yields file."""
    with _LLAMA_LOCK:
        fresh = bool(_LLAMA_CACHE["rows"]) and time.time() - _LLAMA_CACHE["ts"] < _LLAMA_TTL
        if fresh or getattr(warm_apr_cache, "_running", False):
            return
        warm_apr_cache._running = True  # type: ignore[attr-defined]

    def _run() -> None:
        try:
            _refresh_llama()
        finally:
            warm_apr_cache._running = False  # type: ignore[attr-defined]

    threading.Thread(target=_run, name="llama-apr", daemon=True).start()


def llama_rows() -> list[dict]:
    warm_apr_cache()
    return list(_LLAMA_CACHE["rows"])


def coinbase_eth_usd8() -> int:
    resp = requests.get(COINBASE_TICKER, headers={"User-Agent": "argon-agent"}, timeout=3)
    resp.raise_for_status()
    return int(float(resp.json()["price"]) * 1e8)


def match_apr(cfg: ChainCfg, pool: str) -> dict:
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
    return best or dict(_EMPTY_APR)


_SWAP_TOPIC = Web3.keccak(text="Swap(address,address,int256,int256,uint160,uint128,int24)")
_UNI_APR_CACHE: dict[str, tuple[float, dict]] = {}
_UNI_APR_TTL = 600.0


def uniswap_fee_apr(
    w3: Web3,
    pool: str,
    *,
    token0: str,
    weth: str,
    stable: str,
    stable_decimals: int,
    eth_usd8: int,
    fee_ppm: int,
    tvl_usd: float,
) -> dict | None:
    """Fee APR from the Uniswap v3 pool's own Swap logs, scaled to 24h."""
    key = pool.lower()
    cached = _UNI_APR_CACHE.get(key)
    now = time.time()
    if cached and now - cached[0] < _UNI_APR_TTL:
        return cached[1]
    if tvl_usd <= 0 or eth_usd8 <= 0:
        return None
    latest = int(w3.eth.block_number)
    head = w3.eth.get_block(latest)
    anchor = max(1, latest - 2_000)
    older = w3.eth.get_block(anchor)
    elapsed = max(int(head["timestamp"]) - int(older["timestamp"]), 1)
    blocks_per_sec = 2_000 / elapsed
    span_blocks = min(int(86_400 * blocks_per_sec), 24_000)
    start_block = max(0, latest - span_blocks)
    start_ts = int(w3.eth.get_block(start_block)["timestamp"])
    covered = max(int(head["timestamp"]) - start_ts, 1)
    volume = 0.0
    step = 4_000
    cursor = start_block
    while cursor <= latest:
        end = min(latest, cursor + step - 1)
        try:
            logs = w3.eth.get_logs(
                {
                    "address": Web3.to_checksum_address(pool),
                    "fromBlock": cursor,
                    "toBlock": end,
                    "topics": [_SWAP_TOPIC],
                }
            )
        except Exception:
            log.exception("Uniswap swap logs failed %s %s-%s", pool, cursor, end)
            logs = []
        for entry in logs:
            raw = entry["data"]
            payload = bytes(raw)
            volume += swap_volume_usd(
                payload,
                token0=token0,
                weth=weth,
                stable=stable,
                stable_decimals=stable_decimals,
                eth_usd8=eth_usd8,
            )
        cursor = end + 1
    volume_24h = volume * (86_400 / covered)
    apr = fee_apr_pct(volume_24h, tvl_usd, fee_ppm)
    if apr is None:
        return None
    result = {
        "aprPct": apr,
        "aprBasePct": apr,
        "aprSource": "uniswap",
        "llamaTvlUsd": None,
        "volumeUsd1d": volume_24h,
    }
    _UNI_APR_CACHE[key] = (now, result)
    log.info("Uniswap fee APR %s %.2f%% on $%.0f 24h volume", pool, apr, volume_24h)
    return result


def describe_pool(cfg: ChainCfg, eth_usd8: int) -> dict:
    pair = "WETH/USDC" if cfg.name == "arbitrum" else "WETH/USDG"
    stable_symbol = "USDC" if cfg.name == "arbitrum" else "USDG"
    w3 = _w3(cfg)
    if not w3.is_connected():
        raise RuntimeError(f"{cfg.name} RPC not connected")
    vault = w3.eth.contract(address=Web3.to_checksum_address(VAULT), abi=VAULT_ABI)
    weth = Web3.to_checksum_address(vault.functions.weth().call())
    stable = Web3.to_checksum_address(vault.functions.stable().call())
    decimals = int(vault.functions.stableDecimals().call())
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
    try:
        apr = match_apr(cfg, pool_addr)
    except Exception:
        log.exception("APR lookup failed on %s", cfg.name)
        apr = dict(_EMPTY_APR)
    if apr.get("aprPct") is None:
        try:
            uni = uniswap_fee_apr(
                w3,
                pool_addr,
                token0=token0,
                weth=weth,
                stable=stable,
                stable_decimals=decimals,
                eth_usd8=eth_usd8,
                fee_ppm=cfg.fee,
                tvl_usd=usd8_to_float(tvl8),
            )
            if uni:
                apr = uni
        except Exception:
            log.exception("Uniswap fee APR failed on %s", cfg.name)
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
    now = time.time()
    cached = _SNAPSHOT.get("body")
    if cached and now - float(_SNAPSHOT["ts"]) < _SNAPSHOT_TTL:
        return cached
    warm_apr_cache()
    try:
        eth_usd8 = coinbase_eth_usd8()
    except Exception:
        log.exception("Coinbase ETH price failed")
        eth_usd8 = 0
    items = []
    for cfg in chains():
        if not cfg.enabled:
            continue
        try:
            items.append(describe_pool(cfg, eth_usd8))
        except Exception as exc:
            log.exception("pool describe failed on %s", cfg.name)
            items.append(
                {
                    "id": cfg.name,
                    "chainId": cfg.chain_id,
                    "poolId": cfg.pool_id,
                    "pair": "WETH/USDC" if cfg.name == "arbitrum" else "WETH/USDG",
                    "error": str(exc),
                    "selectable": False,
                    "inPool": False,
                    "poolTvlUsd": None,
                    "ethUsd": None,
                    "aprPct": None,
                    "aprSource": "unavailable",
                }
            )
    body = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "pollSeconds": 15,
        "selectOneChain": True,
        "pools": items,
    }
    _SNAPSHOT["ts"] = now
    _SNAPSHOT["body"] = body
    return body
