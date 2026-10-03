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

# Live addresses differ per chain. Override with ARB_/RH_ + VAULT_ADDRESS etc.
ARB_REGISTRY = os.getenv("ARB_REGISTRY_ADDRESS", "0x8F288a7a6E28a5d44980De19502522C376965afe")
ARB_VAULT = os.getenv("ARB_VAULT_ADDRESS", "0xe0eb546A1F8dcEc7B124cF8fE253de34d54A6c61")
ARB_ADAPTER = os.getenv("ARB_ADAPTER_ADDRESS", "0x05734481536644bc20e671Db28f5b4c05B7D64D4")
RH_REGISTRY = os.getenv("RH_REGISTRY_ADDRESS", "0x256A61b459BFdb48B4C04DE5Ba13E0dFBC326508")
RH_VAULT = os.getenv("RH_VAULT_ADDRESS", "0x89403CA4AdB3A89A0173B7494903B4247881966f")
RH_ADAPTER = os.getenv("RH_ADAPTER_ADDRESS", "0xEDa50F3F5530E9BFFD427c1DB0E0a8f3D05cCC9D")

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
    registry: str
    vault: str
    adapter: str


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
            registry=ARB_REGISTRY,
            vault=ARB_VAULT,
            adapter=ARB_ADAPTER,
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
            registry=RH_REGISTRY,
            vault=RH_VAULT,
            adapter=RH_ADAPTER,
        ),
    ]


def tiingo_api_key() -> str:
    return (os.getenv("TIINGO_API_KEY") or "").strip()


def uniswap_api_key() -> str:
    """x-api-key from the Uniswap developer dashboard. Used by /pools."""
    return (os.getenv("UNISWAP_API_KEY") or "").strip()


def keeper_key() -> str | None:
    """Keeper key is for the clock dyno only. The public web process must not load it."""
    dyno = os.getenv("DYNO", "")
    if dyno.startswith("web"):
        return None
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
