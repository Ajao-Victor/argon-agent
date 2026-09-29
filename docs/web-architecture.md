# Argon — product architecture and web development spec

This is the document for building the dApp. It covers product architecture, how the website talks to the price agent, and how the website talks to the Arbitrum (and Robinhood) contracts.

**Audience:** frontend / full-stack web.  
**Locked product:** Argon. ETH-only model for v1. LINK later.

Related: root [README.md](../README.md). Interaction diagram: [Web × agent × contracts](/Users/wang/.cursor/projects/Users-wang-Untitled/canvases/argon-web-architecture.canvas.tsx) (open beside chat).

---

## 1. What the product is

A user deposits tokens into a vault. They do **not** mint Uniswap LP themselves.

Every hour a hosted model infers **ETH % change at 1h, 2h, and 8h**. The keeper uses this gate:

| Condition | Action | UI |
|-----------|--------|-----|
| `\|1h\| ≥ 1%` **or** `\|2h\| ≥ 2.5%` | `EXIT` | `IDLE` |
| idle and `\|1h\| < 1%` **and** `\|2h\| < 2.5%` **and** `\|8h\| < 2%` | `ENTER` | `IN_POOL` |
| in pool and not EXIT | `HOLD` | `IN_POOL` |

Hours **0–7** are warmup: forecasts stored, **no trades**. Hour **8** is the first enter/exit.

The website is the user’s window: wallet, deposit/withdraw, live forecast, pool status, and the on-chain hash that proves the vault used the same number Heroku produced.

The website is **not** the keeper. The user’s wallet never calls `rebalance()`.

---

## 2. System architecture

Three planes. The web app sits on all three as a **reader** plus **user custody** (deposit/withdraw only).

```
                    ┌─────────────────────────────────────┐
                    │  Website (Next.js)                  │
                    │  wagmi + viem                       │
                    │  deposit / withdraw / display       │
                    └──────────┬──────────────┬───────────┘
                               │              │
                    HTTPS JSON │              │ wallet txs
                               ▼              ▼
              ┌────────────────────┐   ┌─────────────────────┐
              │ Heroku agent API   │   │ Arbitrum One 42161  │
              │ GET /forecasts     │   │ Vault + Registry    │
              │ GET /status        │   │ Uniswap v3 / v4     │
              └─────────┬──────────┘   └──────────▲──────────┘
                        │                         │
                        │ hourly clock            │ keeper txs
                        │ load pickle/h5          │ (not the website)
                        ▼                         │
              ┌────────────────────┐              │
              │ Postgres           │              │
              │ 8h rolling window  │──────────────┘
              └────────────────────┘     also: Robinhood 4663
                                         Vault (WETH/USDG)
```

| Plane | Owns | Web talks to it how |
|-------|------|---------------------|
| **Agent** (Heroku) | Model file, hourly infer, forecast store, dual-horizon gate | `fetch` REST. Read-only. |
| **Contracts** (Arbitrum, Robinhood) | User funds, Uniswap positions, public forecast hash | wagmi/viem. User signs deposit/withdraw only. |
| **Keeper** (Heroku clock or separate worker) | `submit()` + `rebalance()` | Web **never** holds this key. Web **reads** the resulting txs. |

Custody is on-chain. Judgment is off-chain. The keeper is the only writer from agent → chain.

v1 does **not** bridge between Arbitrum and Robinhood. If the user wants pool 4, they deposit on Robinhood separately. Same UI, two vaults.

---

## 3. Web ↔ agent (price model)

### 3.1 What the model returns

The Python pipeline must emit **three** horizons each hour (1h and 2h are extra heads; do not divide the 8h print by 8):

```text
percentage_change = (exp(log_return) - 1) * 100
```

Example payload:

```json
{
  "hourId": 488888,
  "submittedAt": "2026-09-16T16:00:00.000Z",
  "ethPct1h": -0.40,
  "ethPct2h": -1.10,
  "ethPct8h": -1.50,
  "spotUsd": 2410.12,
  "modelId": "eth-1-2-8h-v1",
  "status": "pending",
  "action": "enter",
  "gate1hBps": 100,
  "gate2hBps": 250,
  "gate8hBps": 200,
  "warmupComplete": true,
  "txHash": "0xabc...",
  "forecastHash": "0xdef..."
}
```

| Field | Meaning for the UI |
|-------|--------------------|
| `ethPct1h` / `ethPct2h` / `ethPct8h` | The three numbers. Drive chips and copy |
| `action` | Server-computed: `exit` \| `enter` \| `hold` \| `warmup` |
| `warmupComplete` | `false` until hour 8 |
| `forecastHash` | Must match `InferenceRegistry` on Arbiscan |

### 3.2 Dual-horizon gate (same rule the keeper uses)

```ts
const GATE_1H = 1.0;   // gate1hBps = 100
const GATE_2H = 2.5;   // gate2hBps = 250
const GATE_8H = 2.0;   // gate8hBps = 200 (ENTER only)

export type PolicyAction = "warmup" | "exit" | "enter" | "hold";

export function policyAction(opts: {
  ethPct1h: number;
  ethPct2h: number;
  ethPct8h: number;
  warmupComplete: boolean;
  currentlyInPool: boolean;
}): PolicyAction {
  if (!opts.warmupComplete) return "warmup";
  const exit =
    Math.abs(opts.ethPct1h) >= GATE_1H ||
    Math.abs(opts.ethPct2h) >= GATE_2H;
  if (exit) return "exit";
  const canEnter =
    Math.abs(opts.ethPct1h) < GATE_1H &&
    Math.abs(opts.ethPct2h) < GATE_2H &&
    Math.abs(opts.ethPct8h) < GATE_8H;
  if (opts.currentlyInPool) return "hold";
  return canEnter ? "enter" : "exit"; // idle but 8h outside → stay flat
}
```

If idle and 1h/2h are inside but 8h is outside, action is **stay idle** (treat as `exit` / `IDLE`, do not mint). Prefer the API `action` field in the UI.

### 3.3 REST API the website calls

Base URL: `NEXT_PUBLIC_AGENT_URL` (e.g. `https://argon-agent.herokuapp.com`).

CORS: allow the web origin only.

| Method | Path | Auth | Use |
|--------|------|------|-----|
| `GET` | `/health` | none | Agent up, model loaded |
| `GET` | `/status` | none | Warmup, last hour, last error |
| `GET` | `/forecasts/latest` | none | Hero forecast on the dashboard |
| `GET` | `/forecasts?limit=24` | none | History chart / table |
| `GET` | `/forecasts/:hourId` | none | Detail + realized vs predicted |

**`GET /status`**

```json
{
  "ok": true,
  "warmupComplete": false,
  "hoursUntilFirstDecision": 5,
  "gate1hBps": 100,
  "gate2hBps": 250,
  "gate8hBps": 200,
  "lastHourId": 488883,
  "modelId": "eth-1-2-8h-v1",
  "modelLoaded": true
}
```

**`GET /forecasts/latest`** — one object as in §3.1.

**`GET /forecasts?limit=24`** — `{ "items": [ ... ] }` newest first.

The website **never** `POST`s an inference. Only the Heroku clock loads the pickle/h5.

Poll `/forecasts/latest` every 30–60s, and again at `:01` UTC. Do not spin a 1s loop.

### 3.4 Client module (suggested)

`apps/web/lib/agent.ts`

```ts
const BASE = process.env.NEXT_PUBLIC_AGENT_URL!;

export async function getLatestForecast() {
  const res = await fetch(`${BASE}/forecasts/latest`, { next: { revalidate: 30 } });
  if (!res.ok) throw new Error(`agent ${res.status}`);
  return res.json() as Promise<Forecast>;
}
```

Use a small React Query / SWR hook `useLatestForecast()` so every page shares one cache.

If the agent is down: show the last on-chain `InferenceRegistry` row and a banner “live agent unreachable — showing last on-chain forecast.” Do not fake a %.

---

## 4. Web ↔ smart contracts (Arbitrum)

### 4.1 Networks the dApp must support

| Network | Chain ID | RPC (default) | Explorer |
|---------|----------|---------------|----------|
| Arbitrum One | `42161` | `https://arb1.arbitrum.io/rpc` | https://arbiscan.io |
| Robinhood Chain | `4663` | `https://rpc.mainnet.chain.robinhood.com` | https://robinhoodchain.blockscout.com |

Add both to wagmi `createConfig`. Default the UI to Arbitrum. A network switcher is required for pool 4.

### 4.2 Tokens (checksummed)

**Arbitrum One**

| Token | Address | Decimals |
|-------|---------|----------|
| WETH | `0x82aF49447D8a07e3bd95BD0d56f35241523fBab1` | 18 |
| USDC | `0xaf88d065e77c8cC2239327C5EDb3A432268e5831` | 6 |
| LINK | `0xf97f4df75117a78c1A5a0DBb814Af92458539FB4` | 18 |

**Robinhood Chain**

| Token | Address | Decimals |
|-------|---------|----------|
| WETH | `0x0bd7d308f8e1639fab988df18a8011f41eacad73` | 18 |
| USDG | `0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 6 |

Native ETH can be wrapped to WETH in the deposit flow (one extra tx or a vault `depositETH()` if the contract exposes it).

### 4.3 Pools the UI lists

v1 ETH gate applies to **1 and 4** only. Pools 2 and 3 are listed as “LINK — model later” (disabled or read-only).

| ID | Pair | Chain | DEX | ETH gate |
|----|------|-------|-----|----------|
| 1 | WETH / USDC | Arbitrum | Uniswap v3 | yes |
| 2 | LINK / WETH | Arbitrum | Uniswap v3 | no (later) |
| 3 | LINK / USDC | Arbitrum | Uniswap v4 | no (later) |
| 4 | WETH / USDG | Robinhood | Uniswap v3 | yes |

### 4.4 Contracts the web must bind

Addresses come from env after deploy (`NEXT_PUBLIC_VAULT_ARB`, etc.). Until then, use placeholders and a “not deployed” empty state.

**`InferenceRegistry` (both chains, same ABI)**

```solidity
function latestHourId() external view returns (uint64);
function getForecast(uint64 hourId) external view returns (
    int256 ethPctBps,      // e.g. -241 = -2.41%
    uint64 targetHourId,
    bytes32 forecastHash,
    uint64 submittedAt,
    address submitter
);
event ForecastSubmitted(uint64 indexed hourId, int256 ethPctBps, bytes32 forecastHash);
```

Web uses this to **verify** Heroku: `forecastHash` and `ethPctBps` on the dashboard must match the API row. Link `tx` to Arbiscan.

**`ArgonVault` (per chain)**

User-facing:

```solidity
function deposit(address token, uint256 amount) external;
function depositETH() external payable;                    // optional
function withdraw(address token, uint256 amount) external;
function emergencyWithdraw() external;                     // idle balances, always
function idleBalance(address user, address token) external view returns (uint256);
function shareBalance(address user) external view returns (uint256);
function poolStatus(uint8 poolId) external view returns (uint8); // 0 idle, 1 in pool
function gate1hBps() external view returns (uint16);  // 100
function gate2hBps() external view returns (uint16);  // 250
function gate8hBps() external view returns (uint16);  // 200
function warmupComplete() external view returns (bool);
```

Keeper-only (do **not** expose buttons): `rebalance(uint8 poolId, uint8 action, ...)`.

Events to index in the activity feed:

```solidity
event Deposited(address indexed user, address token, uint256 amount);
event Withdrawn(address indexed user, address token, uint256 amount);
event Rebalanced(uint64 indexed hourId, uint8 poolId, uint8 action, bytes32 forecastHash);
```

### 4.5 Deposit / withdraw flow (user wallet)

1. `useAccount` + `useSwitchChain` to the vault’s chain.
2. If ERC-20: `approve(vault, amount)` then `deposit(token, amount)`.
3. If ETH: `depositETH()` with `value`.
4. Wait for receipt. Invalidate wagmi reads (`idleBalance`, `shareBalance`).
5. Withdraw is the inverse. Disable withdraw of funds that are **in pool** unless `emergencyWithdraw` is specified to only idle — copy must say “idle balances only” unless the vault also exits LP on user withdraw (prefer: user withdraw = idle only; they wait for next EXIT if they want everything out — **or** a `withdrawAll` that requests flatten. v1: idle-only withdraw + banner “funds in range unlock on next EXIT”).

Recommended v1 UX: **Withdraw idle now**. If status is `IN_POOL`, show “Your LP is in range. Withdraw becomes available when the model next exits, or use Emergency idle withdraw for any unallocated tokens.”

### 4.6 What the frontend must never call

- `rebalance`
- `submit` on the registry
- Uniswap `NonfungiblePositionManager` / v4 `PoolManager` directly
- The Heroku clock URL / keeper private key

If a connected wallet is the keeper (demo), hide those behind an admin flag `NEXT_PUBLIC_ADMIN_ADDRESS`. Default app users do not see it.

---

## 5. Pages and UI inventory

Stack: **Next.js (App Router)**, **TypeScript**, **wagmi v2**, **viem**, **TanStack Query**, wallet modal (RainbowKit or ConnectKit). Tailwind is fine.

### 5.1 Routes

| Route | Purpose |
|-------|---------|
| `/` | Landing: one-liner, 1h/2h/8h gate, “Launch app” |
| `/app` | Dashboard: forecast hero, warmup countdown, four pool cards, last hash |
| `/app/deposit` | Chain switcher, token amounts, approve + deposit |
| `/app/withdraw` | Idle balances, withdraw |
| `/app/forecasts` | Table of last 24 hours, predicted vs realized after maturity |
| `/app/activity` | Deposit / withdraw / rebalance events for this wallet |

### 5.2 Dashboard widgets (required)

1. **Forecast hero**  
   Large `ethPct1h`, `ethPct2h`, `ethPct8h`. Action chip `ENTER` / `EXIT` / `HOLD` / `WARMUP`. Mark which horizon tripped EXIT.

2. **Warmup**  
   Progress `n / 8` hours until first decision. No fake IN_POOL during warmup.

3. **Pool cards** (four)  
   Pair, chain, `IN_POOL` | `IDLE` | `UNFUNDED` | `LINK_SOON`. Link to explorer for the last rebalance tx.

4. **On-chain match**  
   `forecastHash` from API vs registry. Green “matches chain” or red “API ≠ registry”.

5. **Wallet strip**  
   Address, chain, idle WETH/USDC (or USDG) in the vault.

### 5.3 Copy for the gate

- EXIT: “Near-term ETH move looks large (1h ≥ 1% or 2h ≥ 2.5%). Positions flattened.”
- ENTER: “1h, 2h, and 8h all inside band. Liquidity in range.”
- Idle because 8h is outside: “Near path is calm, but 8h is outside ±2%. Staying in cash.”
- Warmup: “Collecting the first 8 hourly forecasts. No trades until hour 8.”

### 5.4 Empty / error states (build these, not only the happy path)

- Wallet not connected
- Wrong chain
- Agent 5xx / timeout
- Registry not deployed
- Warmup
- User has no deposit
- Approve needed
- Tx rejected
- Sequencer / stale oracle (if vault view reverts with a reason, surface it)

### 5.5 Suggested component tree

```
app/
  layout.tsx                 Providers: wagmi, query, wallet
  page.tsx                   Landing
  app/page.tsx               Dashboard
  app/deposit/page.tsx
  app/withdraw/page.tsx
  app/forecasts/page.tsx
  app/activity/page.tsx
lib/
  agent.ts                   REST client
  policy.ts                  dual-horizon helper (tests)
  chains.ts                  arb + robinhood
  tokens.ts                  addresses + decimals
  contracts.ts               ABIs + addresses from env
  format.ts                  bps ↔ %, hourId → UTC
hooks/
  useLatestForecast.ts
  useForecastHistory.ts
  useVaultBalances.ts
  usePoolStatus.ts
  useRegistryForecast.ts
  useDeposit.ts
  useWithdraw.ts
components/
  ConnectButton.tsx
  ChainSwitcher.tsx
  ForecastHero.tsx
  WarmupBar.tsx
  PoolCard.tsx
  HashMatch.tsx
  TokenAmountInput.tsx
```

---

## 6. Env vars (web)

```bash
NEXT_PUBLIC_AGENT_URL=https://argon-agent.herokuapp.com

NEXT_PUBLIC_ARB_RPC=https://arb1.arbitrum.io/rpc
NEXT_PUBLIC_RH_RPC=https://rpc.mainnet.chain.robinhood.com

NEXT_PUBLIC_VAULT_ARB=0x...
NEXT_PUBLIC_REGISTRY_ARB=0x...
NEXT_PUBLIC_VAULT_RH=0x...
NEXT_PUBLIC_REGISTRY_RH=0x...

NEXT_PUBLIC_WALLETCONNECT_ID=
NEXT_PUBLIC_ADMIN_ADDRESS=           # optional, hide keeper tools
```

Never put the keeper private key or Tiingo key in the web app.

---

## 7. HourId and time (do this once, reuse everywhere)

```ts
export function hourIdFromDate(d = new Date()) {
  return Math.floor(d.getTime() / 1000 / 3600);
}

export function dateFromHourId(hourId: number) {
  return new Date(hourId * 3600 * 1000); // UTC
}
```

Display in UTC with an explicit `UTC` label so it matches the Heroku clock.

On-chain: store 1h/2h/8h as signed bps (`ethPct * 100`), e.g. `-2.41%` → `-241`.

---

## 8. Sequence: one hour as the web sees it

1. User is on `/app`. SWR holds last forecast.
2. Clock hits `:00` UTC. Agent infers, writes Postgres, keeper `submit` + maybe `rebalance`.
3. Next poll: `ethPct1h` / `2h` / `8h` update. `action` flips.
4. `useRegistryForecast(hourId)` confirms hash.
5. `usePoolStatus` flips `IN_POOL` ↔ `IDLE` after the rebalance receipt (often 1–2 extra polls).
6. User does not click. If they deposited earlier, balances stay in the vault; only status chips change.

Deposit can happen any hour, including warmup. Funds sit idle until the first in-gate `ENTER` after hour 8.

---

## 9. wagmi sketch

`lib/chains.ts` — register Robinhood as a custom chain (`id: 4663`).  
`lib/wagmi.ts` — `http()` transports from env RPCs.  
Reads: `useReadContract` for vault + registry.  
Writes: `useWriteContract` + `useWaitForTransactionReceipt` for approve/deposit/withdraw only.

Prefer public RPCs from env so local dev does not hardcode.

---

## 10. Out of scope for web v1

- Training UI or uploading pickle/h5
- Calling Tiingo / DIA from the browser
- Cross-chain bridge widget
- LINK enter/exit automation
- Keeper / rebalance buttons for normal users
- Replacing the gate bands in the UI (vault has `gate1hBps` / `gate2hBps` / `gate8hBps`)

---

## 11. Build order for the web track

1. Scaffold Next.js + wagmi + both chains + connect wallet.  
2. Agent types + `useLatestForecast` against a mocked `/forecasts/latest`.  
3. Dashboard: ForecastHero, WarmupBar, four PoolCards (status mocked).  
4. HashMatch once registry is deployed.  
5. Deposit / withdraw against the Arbitrum vault.  
6. Robinhood chain switch + vault 4.  
7. Forecasts history table with matured realized %.  
8. Activity feed from events.

Mock the agent with a static JSON fixture until Heroku is live so UI work is not blocked.

---

## 12. Acceptance checks (web)

- Connect on Arbitrum; deposit USDC; idle balance updates.  
- Dashboard shows 1h, 2h, and 8h % from the agent.  
- `pred_2h = −2.8%` shows EXIT / IDLE even if 1h is small.  
- All three inside band shows ENTER/HOLD / IN_POOL.  
- Idle + 8h outside (1h/2h inside) stays `IDLE`, not `IN_POOL`.  
- Warmup hides trade status until 8 hours.  
- `forecastHash` matches Arbiscan when both API and registry are live.  
- Withdraw of idle works; in-pool funds are not silently stolen by a broken withdraw button.  
- Robinhood network switch does not break Arbitrum reads (separate vault addresses).
