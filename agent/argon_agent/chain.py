"""Keeper: registry.submit then vault.rebalance on Arbitrum and Robinhood."""

from __future__ import annotations

import logging
from typing import Any

from eth_account import Account
from web3 import Web3

from argon_agent.abis import ADAPTER_ABI, REGISTRY_ABI, VAULT_ABI
from argon_agent.config import (
    ADAPTER,
    DRY_RUN,
    REBALANCE_ONCHAIN,
    REGISTRY,
    SUBMIT_ONCHAIN,
    VAULT,
    ChainCfg,
    chains,
    keeper_key,
)
from argon_agent.ticks import current_range

log = logging.getLogger("argon.chain")

HOLD, ENTER_CODE, EXIT = 0, 1, 2


class ChainClient:
    def __init__(self, cfg: ChainCfg, account) -> None:
        self.cfg = cfg
        self.account = account
        self.w3 = Web3(Web3.HTTPProvider(cfg.rpc, request_kwargs={"timeout": 30}))
        self.registry = self.w3.eth.contract(
            address=Web3.to_checksum_address(REGISTRY), abi=REGISTRY_ABI
        )
        self.vault = self.w3.eth.contract(address=Web3.to_checksum_address(VAULT), abi=VAULT_ABI)
        self.adapter = self.w3.eth.contract(
            address=Web3.to_checksum_address(ADAPTER), abi=ADAPTER_ABI
        )

    def connected(self) -> bool:
        try:
            return bool(self.w3.is_connected())
        except Exception:
            return False

    def in_pool(self) -> bool:
        try:
            return bool(self.adapter.functions.inPosition().call())
        except Exception:
            try:
                return int(self.vault.functions.poolStatus(self.cfg.pool_id).call()) == 1
            except Exception:
                log.exception("pool status read failed on %s", self.cfg.name)
                return False

    def onchain_warmup_complete(self) -> bool | None:
        try:
            return bool(self.registry.functions.warmupComplete().call())
        except Exception:
            return None

    def forecast_count(self) -> int | None:
        try:
            return int(self.registry.functions.forecastCount().call())
        except Exception:
            return None

    def _send(self, fn) -> str:
        nonce = self.w3.eth.get_transaction_count(self.account.address)
        tx_fields = {
            "from": self.account.address,
            "nonce": nonce,
            "chainId": self.cfg.chain_id,
            "gas": 1_800_000,
        }
        try:
            tx_fields["gas"] = int(fn.estimate_gas({"from": self.account.address}) * 1.25)
        except Exception:
            pass
        tx = fn.build_transaction(tx_fields)
        try:
            latest = self.w3.eth.get_block("latest")
            base_fee = latest.get("baseFeePerGas")
            if base_fee is not None:
                prio = max(int(base_fee) // 10, 1_000_000)
                tx["maxPriorityFeePerGas"] = prio
                tx["maxFeePerGas"] = int(base_fee) * 2 + prio
                tx.pop("gasPrice", None)
            else:
                tx["gasPrice"] = int(self.w3.eth.gas_price)
        except Exception:
            tx["gasPrice"] = int(self.w3.eth.gas_price)
        signed = self.account.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        tx_hash = self.w3.eth.send_raw_transaction(raw)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        if receipt.status != 1:
            raise RuntimeError(f"{self.cfg.name} tx reverted {tx_hash.hex()}")
        hexed = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
        return hexed if hexed.startswith("0x") else "0x" + hexed

    def submit(
        self,
        hour_id: int,
        pct1h_bps: int,
        pct2h_bps: int,
        pct8h_bps: int,
        forecast_hash: bytes,
    ) -> str | None:
        if DRY_RUN or not SUBMIT_ONCHAIN:
            log.info("DRY_RUN skip submit on %s", self.cfg.name)
            return None
        latest = int(self.registry.functions.latestHourId().call())
        count = int(self.registry.functions.forecastCount().call())
        if count != 0 and hour_id <= latest:
            log.info("%s already has hour %s (latest %s)", self.cfg.name, hour_id, latest)
            return None
        return self._send(
            self.registry.functions.submit(hour_id, pct1h_bps, pct2h_bps, pct8h_bps, forecast_hash)
        )

    def rebalance(self, hour_id: int, action: int) -> str | None:
        if DRY_RUN or not REBALANCE_ONCHAIN:
            log.info("DRY_RUN skip rebalance on %s", self.cfg.name)
            return None
        if action == HOLD:
            tick_lower, tick_upper = 0, 10
        elif action == ENTER_CODE:
            tick_lower, tick_upper = current_range(
                self.w3, self.cfg.npm, self.cfg.token_a, self.cfg.token_b, self.cfg.fee
            )
        else:
            tick_lower, tick_upper = 0, 10
        return self._send(
            self.vault.functions.rebalance(
                hour_id, self.cfg.pool_id, action, tick_lower, tick_upper, 0, 0
            )
        )


def registry_forecast_count() -> int | None:
    """View-only. Does not need the keeper key."""
    cfgs = [c for c in chains() if c.enabled]
    if not cfgs:
        return None
    try:
        w3 = Web3(Web3.HTTPProvider(cfgs[0].rpc, request_kwargs={"timeout": 20}))
        reg = w3.eth.contract(address=Web3.to_checksum_address(REGISTRY), abi=REGISTRY_ABI)
        return int(reg.functions.forecastCount().call())
    except Exception:
        log.exception("registry forecastCount read failed")
        return None


def clients() -> list[ChainClient]:
    key = keeper_key()
    if not key:
        return []
    account = Account.from_key(key)
    out: list[ChainClient] = []
    for cfg in chains():
        if not cfg.enabled:
            continue
        client = ChainClient(cfg, account)
        if not client.connected():
            log.warning("RPC not connected: %s %s", cfg.name, cfg.rpc)
            continue
        out.append(client)
    return out


def any_in_pool(live: list[ChainClient]) -> bool:
    return any(c.in_pool() for c in live)


def execute_hour(
    live: list[ChainClient],
    hour_id: int,
    pct1h_bps: int,
    pct2h_bps: int,
    pct8h_bps: int,
    forecast_hash: bytes,
    action: int,
    warmup_complete: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "submit": {},
        "rebalance": {},
        "pool_status": {},
    }
    for c in live:
        try:
            tx = c.submit(hour_id, pct1h_bps, pct2h_bps, pct8h_bps, forecast_hash)
            result["submit"][c.cfg.name] = tx
        except Exception as exc:
            log.exception("submit failed on %s", c.cfg.name)
            result["submit"][c.cfg.name] = f"error:{exc}"
        try:
            if warmup_complete:
                rtx = c.rebalance(hour_id, action)
                result["rebalance"][c.cfg.name] = rtx
            else:
                result["rebalance"][c.cfg.name] = "warmup-skip"
        except Exception as exc:
            log.exception("rebalance failed on %s", c.cfg.name)
            result["rebalance"][c.cfg.name] = f"error:{exc}"
        try:
            result["pool_status"][c.cfg.name] = 1 if c.in_pool() else 0
        except Exception:
            result["pool_status"][c.cfg.name] = None
    return result
