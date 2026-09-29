"""Runtime config from environment. Never put keys in git."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = Path(os.getenv("MODELS_DIR", ROOT / "models"))

MODEL_ID_TEXT = os.getenv("MODEL_ID", "eth-1-2-8h-v1")
GATE_1H_BPS = int(os.getenv("GATE_1H_BPS", "100"))
GATE_2H_BPS = int(os.getenv("GATE_2H_BPS", "250"))
GATE_8H_BPS = int(os.getenv("GATE_8H_BPS", "200"))
WARMUP_SUBMITS = 9

# Live CREATE addresses (same on Arb and Robinhood).
REGISTRY = os.getenv("REGISTRY_ADDRESS", "0xbAf00c0aCa440337d43495c7de661A0AC2E01e8f")
VAULT = os.getenv("VAULT_ADDRESS", "0x9F844b4D1b28Be7413067f9d4fC08Bc276fd1C60")
ADAPTER = os.getenv("ADAPTER_ADDRESS", "0xECCc4B8946D0DB206f977d3021544D0cD5Dc69D4")

ARB_CHAIN_ID = 42161
RH_CHAIN_ID = 4663
ARB_POOL_ID = 1
RH_POOL_ID = 4

ARB_RPC = os.getenv("ARB_RPC", "https://arb1.arbitrum.io/rpc")
RH_RPC = os.getenv("RH_RPC", "https://rpc.mainnet.chain.robinhood.com")

ARB_NPM = os.getenv("ARB_NPM", "0xC36442b4a4522E871399CD717aBDD847Ab11FE88")
RH_NPM = os.getenv("RH_NPM", "0x73991a25C818Bf1f1128dEAaB1492D45638DE0D3")
ARB_WETH = os.getenv("ARB_WETH", "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
ARB_USDC = os.getenv("ARB_USDC", "0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
RH_WETH = os.getenv("RH_WETH", "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73")
RH_USDG = os.getenv("RH_USDG", "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168")
ARB_FEE = int(os.getenv("ARB_FEE", "500"))
RH_FEE = int(os.getenv("RH_FEE", "500"))

TICK_HALF_WIDTH = int(os.getenv("TICK_HALF_WIDTH", "200"))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")
SUBMIT_ONCHAIN = os.getenv("SUBMIT_ONCHAIN", "true").lower() in ("1", "true", "yes")
REBALANCE_ONCHAIN = os.getenv("REBALANCE_ONCHAIN", "true").lower() in ("1", "true", "yes")


def _truthy(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class ChainCfg:
    name: str
    chain_id: int
    rpc: str
    pool_id: int
    npm: str
    token_a: str
    token_b: str
    fee: int
    enabled: bool


def chains() -> list[ChainCfg]:
    return [
        ChainCfg(
            name="arbitrum",
            chain_id=ARB_CHAIN_ID,
            rpc=ARB_RPC,
            pool_id=ARB_POOL_ID,
            npm=ARB_NPM,
            token_a=ARB_WETH,
            token_b=ARB_USDC,
            fee=ARB_FEE,
            enabled=_truthy("ENABLE_ARBITRUM", "true"),
        ),
        ChainCfg(
            name="robinhood",
            chain_id=RH_CHAIN_ID,
            rpc=RH_RPC,
            pool_id=RH_POOL_ID,
            npm=RH_NPM,
            token_a=RH_WETH,
            token_b=RH_USDG,
            fee=RH_FEE,
            enabled=_truthy("ENABLE_ROBINHOOD", "true"),
        ),
    ]


def tiingo_api_key() -> str:
    return (os.getenv("TIINGO_API_KEY") or "").strip()


def keeper_key() -> str | None:
    raw = (os.getenv("KEEPER_PRIVATE_KEY") or "").strip()
    if not raw:
        return None
    return raw if raw.startswith("0x") else "0x" + raw


def database_url() -> str | None:
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def frontend_origins() -> list[str]:
    raw = os.getenv("FRONTEND_ORIGIN", "*")
    return [p.strip() for p in raw.split(",") if p.strip()]


def pickle_path(horizon: str) -> Path:
    override = os.getenv(f"MODEL_{horizon.upper()}_PATH")
    if override:
        return Path(override)
    return MODELS_DIR / f"eth_{horizon}_lgbm.pkl"


def pickle_url(horizon: str) -> str | None:
    return os.getenv(f"MODEL_{horizon.upper()}_URL") or None


def eight_h_loaded() -> bool:
    return pickle_path("8h").exists() or bool(pickle_url("8h"))
