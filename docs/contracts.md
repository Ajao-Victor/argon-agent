# Argon — smart contract layer

What we deploy on **Arbitrum One (42161)** and **Robinhood Chain (4663)**, and how it plugs into the website and the Heroku agent.

The chain does **custody and execution**. It does **not** run the model. Heroku infers; the keeper is a hot wallet that is only allowed to call a few functions; users only deposit and withdraw.

---

## 1. Where contracts sit in the design

```
User  ──deposit/withdraw──►  ArgonVault  ──mint/burn LP──►  Uniswap (v3 / v4)
                                ▲
                                │ rebalance(ENTER|EXIT|HOLD)
                                │
Heroku clock ──submit(1h,2h,8h)──► InferenceRegistry
       │
       └── policy (off-chain) decides the action, then keeper calls vault
```

| Plane | Runs | Writes on-chain? |
|-------|------|------------------|
| Model + Postgres | Heroku | No |
| Policy (1h/2h/8h gate) | Heroku | No (but vault **re-checks** the numbers) |
| Website | Next.js | Only `deposit` / `withdraw` from the user wallet |
| Keeper | Heroku clock wallet | `registry.submit` then `vault.rebalance` |
| Vault + adapters | Solidity | Holds tokens, owns the Uniswap NFT / v4 position |
| Registry | Solidity | Public forecast for this `hourId` |

If Heroku dies, funds stay in the vault (idle or in-range). Users can still `emergencyWithdraw` **idle** balances. The model cannot steal.

---

## 2. What we are building (contract list)

Same **interfaces** on both chains. Different **constructor args** (tokens, Uniswap addresses, which pool IDs exist).

| Contract | Arbitrum | Robinhood | Job |
|----------|----------|-----------|-----|
| `InferenceRegistry` | yes | yes | Store this hour’s 1h/2h/8h bps + hash. Source of truth the UI and vault both read. |
| `ArgonVault` | yes | yes | User accounting, idle balances, `rebalance` entrypoint, pause, gates. |
| `UniswapV3Adapter` | yes (pools 1, 2) | yes (pool 4) | Talk to `NonfungiblePositionManager`: mint, increase, decrease, collect, burn. |
| `UniswapV4Adapter` | yes (pool 3, later) | no | Talk to v4 `PoolManager` / position manager. Skip in ETH-only v1 if LINK is disabled. |
| `Access` / roles | inside vault + registry | same | `OWNER`, `KEEPER`. Not a separate product. |

Optional tiny helpers (can live on the vault):

- `WETH` wrap/unwrap for `depositETH()`
- Chainlink sequencer-uptime + ETH/USD stale check before `rebalance`

**Not contracts:** the model, Postgres, FastAPI, the Next.js app, Uniswap factories (already deployed).

---

## 3. Each contract

### 3.1 `InferenceRegistry`

Keeper posts the hourly infer so judges and the vault can see the same numbers Heroku used.

```solidity
struct Forecast {
    int256 pct1hBps;   // -40 = -0.40%
    int256 pct2hBps;
    int256 pct8hBps;
    bytes32 forecastHash;
    uint64 submittedAt;
    address submitter;
}

function submit(
    uint64 hourId,
    int256 pct1hBps,
    int256 pct2hBps,
    int256 pct8hBps,
    bytes32 forecastHash
) external onlyKeeper;

function getForecast(uint64 hourId) external view returns (Forecast memory);
function latestHourId() external view returns (uint64);
function forecastCount() external view returns (uint64); // warmup: need 9 rows (hours 0–8)
```

`forecastHash = keccak256(abi.encode(hourId, pct1hBps, pct2hBps, pct8hBps, modelId))`.  
Website: API JSON must match this struct.  
Vault: `rebalance` reads `getForecast(hourId)` and **must not** take an action that contradicts the gate.

One submit per `hourId`. No overwrite (or only owner, for emergencies).

### 3.2 `ArgonVault`

This is the contract users see.

**Users**

- `deposit(token, amount)` / `depositETH()`
- `withdraw(shares)` — flattens LP first, then pays pro-rata WETH + stable
- `emergencyWithdraw()` — same as a full share burn; allowed while keeper is paused

**Views**

- `idleBalance(user, token)`
- `shareBalance(user)` — claim on vault equity (idle + in-LP, pro-rata)
- `poolStatus(poolId)` — `IDLE` or `IN_POOL`
- `gate1hBps()` `gate2hBps()` `gate8hBps()` — 100 / 250 / 200
- `warmupComplete()` — `forecastCount() >= 9`

**Keeper**

```solidity
enum Action { HOLD, ENTER, EXIT }

function rebalance(
    uint64 hourId,
    uint8 poolId,
    Action action,
    int24 tickLower,
    int24 tickUpper,
    uint256 amount0Min,
    uint256 amount1Min
) external onlyKeeper;
```

`rebalance` rules:

1. Not paused; sequencer up; ETH feed not stale.  
2. `warmupComplete()` unless `action == HOLD`.  
3. `hourId == registry.latestHourId()` (this hour’s forecast).  
4. Derive allowed action from stored bps:

```
EXIT  if |1h| >= gate1h OR |2h| >= gate2h
ENTER if idle AND |1h| < gate1h AND |2h| < gate2h AND |8h| < gate8h
HOLD  if in pool AND not EXIT
```

If the keeper passes `ENTER` when the math says `EXIT`, **revert**. The model is off-chain; the **gate is on-chain**.

5. Cooldown: no `EXIT → ENTER` on the same pool within 2 hours unless EXIT is required again.  
6. Call the adapter. Tokens never leave to an arbitrary address.

**Owner**

- set keeper, pause, set gates, set adapter, set pool allowlist  
- timelock later; for hackathon a multisig/owner is enough

### 3.3 `UniswapV3Adapter`

Only the vault may call it. It holds or is approved for the position NFT.

| Action | Uniswap calls |
|--------|----------------|
| `ENTER` | `mint` (or `increaseLiquidity`) around current tick |
| `EXIT` | `decreaseLiquidity` 100% + `collect` + `burn` if empty |
| `HOLD` | `collect` only if fees > gas; optional skip |

Pool allowlist per chain:

| poolId | Chain | Pair | Adapter |
|--------|-------|------|---------|
| 1 | Arbitrum | WETH / USDC | v3 |
| 2 | Arbitrum | LINK / WETH | v3 — **no ETH-gate rebalance in v1** (revert `poolId=2` until LINK model) |
| 3 | Arbitrum | LINK / USDC | v4 — skip deploy in ETH-only v1 |
| 4 | Robinhood | WETH / USDG | v3 |

### 3.4 What we do **not** put on-chain

- Keras/pickle weights  
- Tiingo/DIA  
- Postgres 8h window (realized vs predicted history)  
- Computing 1h/2h/8h % — only **storing** them  
- User’s private keys  

---

## 4. Two deployments, not one bridged vault

| | Arbitrum vault | Robinhood vault |
|--|----------------|-----------------|
| Tokens | WETH, USDC (LINK held, unused) | WETH, USDG |
| Uniswap | Arb v3 NPM (+ v4 later) | RH v3 NPM |
| Pools gated now | `poolId = 1` | `poolId = 4` |
| Registry | own copy, same ABI | own copy |
| Keeper | can be the same EOA, must be funded in ETH on **both** chains | same |

No canonical bridge in v1. User deposits on each chain they want. A Robinhood halt cannot freeze Arbitrum funds.

---

## 5. End-to-end for one hour

1. Heroku builds features, loads h5, gets `pct1h, pct2h, pct8h`.  
2. Writes Postgres (website `GET /forecasts/latest`).  
3. Keeper `registry.submit(hourId, bps…, hash)` on Arb (and RH if that vault has TVL).  
4. Keeper `vault.rebalance(hourId, poolId, action, ticks, minOut)`.  
5. Vault reads registry, checks gate, adapter talks to Uniswap.  
6. Website: API numbers, `getForecast`, `poolStatus`, last `Rebalanced` event. User did not click.

---

## 6. Roles and money flow

```
User USDC ──approve──► Vault.deposit ──► shares (USD-8)
Keeper ENTER ──► adapter.mint ──► Uniswap NFT owned by adapter
Keeper EXIT  ──► adapter.collect ──► idle in vault (claim still in shares)
User withdraw(shares) ──► flatten LP if needed ──► pro-rata WETH + stable
```

Shares: simple pro-rata `totalAssets` (idle + LP amounts at oracle/spot). Do not invent a yield token with transfer hooks in v1 unless needed. Accounting must survive ENTER/EXIT without leaking.

---

## 7. Build order (contracts)

1. `InferenceRegistry` + tests (submit once per hour, views).  
2. `ArgonVault` accounting: deposit, idle withdraw, shares, pause, roles. **No Uniswap yet.**  
3. On-chain gate helper + `rebalance` that only moves a dummy “in pool” flag (unit test the 1h/2h/8h table).  
4. `UniswapV3Adapter` against a fork of Arbitrum (pool 1).  
5. Deploy Arb: registry, vault, adapter, set keeper.  
6. Clone to Robinhood with USDG + pool 4.  
7. Wire Heroku keeper. Skip v4 until LINK.

---

## 8. Addresses to wire at deploy

**Arbitrum:** WETH `0x82aF49447D8a07e3bd95BD0d56f35241523fBab1`, USDC `0xaf88d065e77c8cC2239327C5EDb3A432268e5831`, Uniswap v3 NPM (canonical Arb). Chainlink ETH/USD + sequencer feed.

**Robinhood:** WETH `0x0bd7d308f8e1639fab988df18a8011f41eacad73`, USDG `0x5fc5360d0400a0fd4f2af552add042d716f1d168`, Uniswap v3 NPM `0x73991a25c818bf1f1128deaab1492d45638de0d3`.
