# Argon

Hourly inference that decides when Uniswap LP should be **in the pool** and when it should sit in **cash**.

Argon is a dual-chain LP vault for [Arbitrum Open House Singapore: Online Buildathon](https://www.hackquest.io/hackathons/Arbitrum-Open-House-Singapore-Online-Buildathon). A user deposits liquidity once. Every hour a hosted model publishes an 8h-ahead ETH % change. That number, not a spot stop, decides when Uniswap LP is minted or burned. The user never clicks in and out of ranges.

**Chains:** Arbitrum One (`42161`) and Robinhood Chain (`4663`).  
**DEX:** Uniswap v3 and v4.  
**Forecast now:** ETH % change at 1h, 2h, and 8h, submitted every hour. LINK later.

Module graph, hourly loop, and decision policy are in the [Argon architecture canvas](/Users/wang/.cursor/projects/Users-wang-Untitled/canvases/argon-architecture.canvas.tsx) — open it beside the chat.

Web-dev spec (site × agent × Arbitrum): [docs/web-architecture.md](docs/web-architecture.md).  
Heroku deploy + where the 8h/9-tick window is stored: [docs/heroku-deploy.md](docs/heroku-deploy.md).  
Smart contracts (what we deploy on Arb + Robinhood): [docs/contracts.md](docs/contracts.md).

---

## Problem

Concentrated Uniswap LP earns fees only while the price stays in range. A one-hour dump (ETH toward $2,300, or LINK selling off independently) pushes the position out of range, realizes impermanent loss, and leaves the LP holding the asset that just fell.

Manual LPs cannot sit on the books every hour. Existing automators rebalance ranges; they do not **leave the pool** when the next hour looks red and **re-enter** when it looks green.

## Solution

1. User deposits into a per-chain vault (not directly into Uniswap).
2. Off-chain model infers ETH % change at **1h, 2h, and 8h**, every hour, and commits that inference.
3. Policy: **ENTER** only if all three are inside their bands; **EXIT** if 1h or 2h is outside.
4. A keeper executes mint / increase / decrease / collect / burn through the vault.
5. Idle liquidity stays in the vault until the next in-gate forecast.

Worked example: `pred_1h = −0.4%`, `pred_2h = −1.1%`, `pred_8h = −1.5%` → all inside → **ENTER**. Next hour `pred_2h = −2.8%` → **EXIT** even if 1h is still −0.3%. First LP decision is at hour 8 (warmup).

---

## Four pools

| # | Pair | Chain | DEX | Why it is in the set |
|---|------|-------|-----|----------------------|
| 1 | WETH / USDC | Arbitrum One | Uniswap v3 | ETH vs native Circle USDC. Deep, the core ETH-stable book. |
| 2 | LINK / WETH | Arbitrum One | Uniswap v3 | LINK as a first-class volatile leg, not an ETH side-effect. |
| 3 | LINK / USDC | Arbitrum One | Uniswap v4 | LINK vs stable so LINK can stay in when ETH is the asset that looks red. |
| 4 | WETH / USDG | Robinhood Chain | Uniswap v3 | Robinhood prize-lane pool. USDG is the chain’s native stable (USDC in becomes USDG). |

**Out of scope for v1:** Base `fETH/USDC`, Ethereum-mainnet `ETH/USDT`, and Arbitrum `WETH/USDT0` (redundant with pool 1). Robinhood `LINK/WETH` is a v2 add-on once pool depth is confirmed.

### Canonical tokens

**Arbitrum One**

| Token | Address |
|-------|---------|
| WETH | `0x82aF49447D8a07e3bd95BD0d56f35241523fBab1` |
| USDC | `0xaf88d065e77c8cC2239327C5EDb3A432268e5831` |
| LINK | `0xf97f4df75117a78c1A5a0DBb814Af92458539FB4` |

**Robinhood Chain**

| Token | Address |
|-------|---------|
| WETH | `0x0bd7d308f8e1639fab988df18a8011f41eacad73` |
| USDG | `0x5fc5360d0400a0fd4f2af552add042d716f1d168` |
| LINK | `0x492641f648a4986844848e0befe66d14817bce34` (held for v2; not in the four pools) |

Fee tiers are resolved at implementation against the deepest honest Uniswap book (prefer v3 0.05% / 0.3% where TVL is real; do not chase a thin v4 APR screenshot).

---

## Architecture

Argon splits into **custody (on-chain)** and **judgment (off-chain)**. The agent never holds user keys. Vaults never call a model. The keeper is the only bridge between the two.

```
User wallet ──► dApp ──► Vault (Arb) ──► Uniswap v3 / v4
                    └──► Vault (RH)  ──► Uniswap v3

Market data ─┐
Chainlink ───┴► Inference agent ──► Policy engine ──► Keeper
                                                      │
                                                      ├─► Vault (Arb)
                                                      └─► Vault (RH)
```

### Modules

| Module | Lives | Job |
|--------|-------|-----|
| **dApp** | Off-chain | Connect wallet, deposit / withdraw, show 1h / 2h / 8h ETH %, per-pool status, last keeper tx. |
| **Vault (Arbitrum)** | Solidity, chain 42161 | Escrow WETH, USDC, LINK. Only the keeper role may mint or burn Uniswap positions. Users can always emergency-withdraw idle balances. |
| **Vault (Robinhood)** | Solidity, chain 4663 | Same pattern for WETH and USDG. Separate contract so a Robinhood outage cannot freeze Arbitrum funds. |
| **Uniswap adapters** | On-chain | v3 `NonfungiblePositionManager` for pools 1, 2, 4. v4 `PoolManager` / position manager for pool 3. |
| **Feature pipeline** | Off-chain | Hourly OHLCV, realized vol, pool tick / inventory, Chainlink spot. Builds the model input vector. |
| **Hosted model** | Heroku | Load pickle/h5. Every hour emit `{ ethPct1h, ethPct2h, ethPct8h, hourId }`. Commit a hash on-chain. |
| **Policy engine** | Off-chain | Dual-horizon gate below. Sequencer-uptime and cooldown. |
| **Keeper** | Off-chain signer | Submit the txs. Never a `ONLYOWNER` rug path: it can only call vault `rebalance()` with slippage and range bounds already set in the contract. |
| **Inference registry** | On-chain | `submit(bytes32 forecastHash, uint64 hourId)` so the hourly call is public even if the model weights stay off-chain. |
| **Oracles** | Chainlink | ETH/USD and LINK/USD on both chains, plus the L2 sequencer uptime feed. Vaults refuse to rebalance if the sequencer is down or the feed is stale. |

### Why two vaults, not a bridge

v1 does **not** move capital between Arbitrum and Robinhood in the critical path. The user deposits on each chain they want exposure to. The same agent and the same UI drive both vaults. Bridging USDC → USDG via Across is a later stretch, not required to demo the hourly loop.

---

## Hourly loop

Cadence is one inference per hour, on the hour (UTC).

| Minute | Step | Module |
|--------|------|--------|
| `:00` | Pull candles, Chainlink spot, current ticks, vault balances | Feature pipeline |
| `:01` | Load pickle/h5, infer ETH % at 1h, 2h, and 8h | Hosted model (Heroku) |
| `:01` | `submit(forecastHash, hourId, …)` on both chains | Inference registry |
| `:02` | Dual-horizon gate → EXIT / ENTER / HOLD | Policy engine |
| `:03–:08` | Keeper sends `rebalance(poolId, action, range, minOut)` | Vaults → Uniswap |
| rest of hour | Positions sit. dApp polls status. No further txs unless emergency | — |

On `EXIT`: decrease liquidity, collect fees and tokens into the vault, leave them idle (do not force-swap into a single asset unless the policy asks for it).  
On `ENTER`: mint a concentrated range around the current tick using vault balances.  
On `HOLD`: collect fees only if gas-positive; do not touch the range.

---

## Decision policy

The **model** times exit and entry. There is no spot stop. Gates apply to **predicted** ETH % change from the latest hourly infer.

Vault params (bps):

| Horizon | Param | Band | Role |
|---------|-------|------|------|
| 1h | `gate1hBps = 100` | **±1.0%** | Near path. Tight, or it never fires. |
| 2h | `gate2hBps = 250` | **±2.5%** | Main near-term size gate. |
| 8h | `gate8hBps = 200` | **±2.0%** | ENTER only. Do not open into an 8h dump that is still quiet on 1h/2h. |

```
EXIT  if  |pred_1h| ≥ 1.0%  OR  |pred_2h| ≥ 2.5%
ENTER if  idle AND |pred_1h| < 1.0% AND |pred_2h| < 2.5% AND |pred_8h| < 2.0%
HOLD  if  already in AND not EXIT
```

Do not wait for *both* 1h and 2h to be large before exiting. Do not open unless **all three** are inside.

The 8h head already exists. **1h and 2h must be real model heads** (same features, different targets). Do not split `pred_8h / 8`.

Hours 0–7: store forecasts only (warmup). Hour 8: first enter/exit. LINK later; pools 2 and 3 stay off this ETH gate.

Cooldown: at most one `EXIT→ENTER` round-trip per pool per two hours unless EXIT fires again.

---

## Trust and safety

- Users retain withdraw rights on idle vault balances at all times.
- Keeper cannot send tokens to an arbitrary address; adapters can only talk to Uniswap position managers and the vault.
- Slippage, tick width, and max gas are contract parameters, not keeper discretion.
- Pause + timelock on adapter upgrades.
- Sequencer-uptime and stale-oracle guards on both L2s.
- Forecast hash is on-chain; model weights can stay private without hiding the number that triggered the trade.

---

## Tech stack

| Layer | Choice |
|-------|--------|
| Contracts | Solidity 0.8.x, Foundry. Stylus later if we need a cheaper tick-math helper. |
| Uniswap | v3 NPM on Arb + Robinhood; v4 on Arb for `LINK/USDC`. |
| Oracles | Chainlink Data Feeds + L2 sequencer feed. |
| Model | Pickle or h5 on Heroku. Hourly inference only. |
| Keeper | viem / ethers with a dedicated hot wallet per chain, funded in ETH for gas. |
| dApp | Next.js, wagmi, permissionless wallet connect for 42161 and 4663. |
| RPCs | `https://arb1.arbitrum.io/rpc` and `https://rpc.mainnet.chain.robinhood.com`. |

---

## Planned repo layout

```
contracts/          Foundry project (live on Arb + RH)
agent/              Heroku: pickle infer, Postgres, dual-horizon keeper
apps/web/lib/       Vercel REST + wagmi ABIs (pages still to scaffold)
```

---

## Hackathon fit

| Criterion | How Argon hits it |
|-----------|-------------------|
| Smart contract quality | Thin vaults, no custodian key, Uniswap adapters isolated, oracle + sequencer guards, Foundry tests on mint/burn/emergency withdraw. |
| Product-market fit | LPs already chase these four books; they lack an hourly “get out before the dump” switch. |
| Innovation | Inference-gated LP, not another range rebalancer. LINK is a second market so the agent is not an ETH bot with extra steps. |
| Real problem | Impermanent loss on concentrated ETH and LINK ranges is the actual PnL leak. |
| Prize lanes | Arbitrum One for pools 1–3; Robinhood Chain for pool 4. One product, both reserved tracks. |

---

## Demo script (target)

1. Deposit USDC + WETH on Arbitrum; deposit WETH + USDG on Robinhood.
2. After warmup, an in-gate forecast (1h < 1% and 2h < 2.5% and 8h < 2%) → funded ETH-stable pools show `IN_POOL`.
3. Next hour `pred_2h = −2.8%` → pools 1 and 4 go `IDLE` even if 1h is small.
4. A later hour all three back inside → those vault balances mint LP again.
5. Show the on-chain `forecastHash` matching the UI number.

---

## Status

Contracts are live on Arbitrum One (`42161`) and Robinhood Chain (`4663`). Addresses: [contracts/deployments.md](contracts/deployments.md).

Agent (Heroku infer + Postgres + keeper): [`agent/`](agent/README.md).  
Frontend glue for Vercel: [`apps/web/lib/`](apps/web/lib/agent.ts).

Still to build: Next.js pages (deposit / withdraw / forecast UI) on Vercel, pointed at the Heroku agent URL.
