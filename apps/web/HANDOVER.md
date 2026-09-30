# Frontend handover

The website is not built yet. This folder is everything a Next.js / wagmi app needs to show **live user balances** and forecasts.

Agent base URL (after CORS `FRONTEND_ORIGIN` is your Vercel domain):

```
https://argon-bd8888db5430.herokuapp.com
```

Env: `NEXT_PUBLIC_AGENT_URL` — see `.env.example`.

## Real-time balance (what to poll)

On-chain shares are the source of truth. The agent reads both vaults and returns USD.

| Interval | Call | UI |
|----------|------|-----|
| **10s** | `GET /portfolio/{wallet}` | Hero: `totalUsd`. Per chain: `shareUsd`, `inPool`, idle WETH/stable |
| **30s** | `GET /forecasts/latest` | 1h / 2h / 8h chips, `action` |
| **30s** | `GET /status` | warmup countdown |
| on tx receipt | refetch portfolio | after deposit / withdraw |

Copy-paste hook: `lib/usePortfolio.ts`.

```ts
const { data, totalUsd } = usePortfolio(address);
// data.chains.arbitrum.shareUsd
// data.chains.robinhood.shareUsd
```

`GET /vault` is the same numbers with no wallet (TVL + pool status).

Until a user deposits, `totalUsd` is `0`. That is correct.

## REST contract

### `GET /portfolio/0x…`

```json
{
  "address": "0x…",
  "updatedAt": "2026-09-30T10:00:00+00:00",
  "pollSeconds": 10,
  "totalUsd": 0,
  "chains": {
    "arbitrum": {
      "chainId": 42161,
      "pair": "WETH/USDC",
      "inPool": false,
      "shareUsd": 0,
      "tvlUsd": 0,
      "shares": "0",
      "idleWethFormatted": 0,
      "idleStableFormatted": 0,
      "walletWethFormatted": 0,
      "walletStableFormatted": 0,
      "stableSymbol": "USDC"
    },
    "robinhood": { "chainId": 4663, "pair": "WETH/USDG", "stableSymbol": "USDG" }
  },
  "forecast": { "ethPct8h": -0.4, "action": "warmup" }
}
```

LP value uses the adapter’s stored principal (same as the vault), not a Uniswap slot0 mark. It updates on the next keeper ENTER/EXIT.

## On-chain reads (wagmi) — do this too

Do not trust the API alone for money. After connect, also `useReadContract`:

- `shareBalance(user)` / `totalShares`
- `idleBalance(user, weth)` / `idleBalance(user, usdc|usdg)`
- `poolStatus(poolId)` — Arb `1`, RH `4`
- `oracle.ethUsd8()` for a local USD quote

ABIs: `lib/abis.ts`. Addresses: `lib/chains.ts`.

User txs only: `approve` + `deposit` / `depositETH` / `withdraw`. Never `rebalance` or `submit`.

## Pages to build

| Route | Data |
|-------|------|
| `/app` | `usePortfolio` + `getLatestForecast` |
| `/app/deposit` | wallet balances from portfolio + approve/deposit |
| `/app/withdraw` | `shares` + withdraw |
| `/app/forecasts` | `getForecasts(24)` |

## CORS

Set Heroku `FRONTEND_ORIGIN` to the Vercel production URL. `https://*.vercel.app` previews already match.
