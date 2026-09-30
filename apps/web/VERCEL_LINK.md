# Vercel ↔ Heroku linkage

Code on GitHub `main` is already the source for both sides.  
Heroku app: **argon** → `https://argon-bd8888db5430.herokuapp.com`  
GitHub: `https://github.com/Wangsamuels/argon`

`apps/web/` is the **frontend kit** (lib + env), not a finished Next.js app. On Vercel: create a Next.js project, copy `apps/web/lib/` into it (or set Root Directory to `apps/web` after you scaffold Next.js there).

Do **not** put `KEEPER_PRIVATE_KEY` or `TIINGO_API_KEY` on Vercel.

---

## 1. What you paste **into Vercel**

Vercel project → **Settings → Environment Variables**. Apply to Production, Preview, and Development.

| Variable | Value | Why |
|----------|--------|-----|
| `NEXT_PUBLIC_AGENT_URL` | `https://argon-bd8888db5430.herokuapp.com` | All forecast / portfolio / pools fetches |
| `NEXT_PUBLIC_ARB_RPC` | `https://arb1.arbitrum.io/rpc` | wagmi reads/writes on Arbitrum One |
| `NEXT_PUBLIC_RH_RPC` | `https://rpc.mainnet.chain.robinhood.com` | wagmi on Robinhood Chain |
| `NEXT_PUBLIC_VAULT_ARB` | `0x9F844b4D1b28Be7413067f9d4fC08Bc276fd1C60` | Arb vault (deposit / shares) |
| `NEXT_PUBLIC_REGISTRY_ARB` | `0xbAf00c0aCa440337d43495c7de661A0AC2E01e8f` | Arb forecast board (hash match) |
| `NEXT_PUBLIC_VAULT_RH` | `0x9F844b4D1b28Be7413067f9d4fC08Bc276fd1C60` | RH vault (same address, chain 4663) |
| `NEXT_PUBLIC_REGISTRY_RH` | `0xbAf00c0aCa440337d43495c7de661A0AC2E01e8f` | RH registry |
| `NEXT_PUBLIC_WALLETCONNECT_ID` | from [cloud.walletconnect.com](https://cloud.walletconnect.com) | RainbowKit / ConnectKit project id |
| `NEXT_PUBLIC_ADMIN_ADDRESS` | *(optional)* `0x9642b6D1Db5D1A3B0A61a831099568bbCbC04D4E` | Hide keeper-only UI unless this wallet is connected |

Token addresses the UI needs (can hardcode from `lib/chains.ts`; no env required):

| Token | Chain | Address |
|-------|--------|---------|
| WETH | Arb 42161 | `0x82aF49447D8a07e3bd95BD0d56f35241523fBab1` |
| USDC | Arb 42161 | `0xaf88d065e77c8cC2239327C5EDb3A432268e5831` |
| WETH | RH 4663 | `0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73` |
| USDG | RH 4663 | `0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168` |

`NEXT_PUBLIC_*` is baked in at **build** time. After changing vars, **Redeploy** the Vercel deployment.

---

## 2. What you copy **from Vercel** back to Heroku

After the first Vercel deploy, open the deployment → **Domains**.

| Copy this | Paste on Heroku |
|-----------|-----------------|
| Production origin, e.g. `https://argon.vercel.app` (no trailing slash) | **Settings → Config Vars → `FRONTEND_ORIGIN`** |

If you use a custom domain later, put that origin instead (or comma-separate both):

```
FRONTEND_ORIGIN=https://argon.vercel.app,https://www.yoursite.com
```

Preview deployments (`https://*.vercel.app`) are already allowed in the agent CORS regex. Production **must** be in `FRONTEND_ORIGIN` or the browser will block `fetch` to Heroku.

You do **not** send WalletConnect IDs, RPC URLs, or contract addresses to Heroku. Those stay on Vercel.

---

## 3. Heroku vars the site depends on (already set for infer)

These are **not** Vercel vars. Confirm they stay on the Heroku app **argon**:

| Heroku config var | Role for the website |
|-------------------|----------------------|
| `FRONTEND_ORIGIN` | CORS allowlist for the Vercel origin (the one missing piece) |
| `DATABASE_URL` | Postgres so `/forecasts` and `/portfolio` persist |
| `TIINGO_API_KEY` | Hourly model (clock) |
| `KEEPER_PRIVATE_KEY` | On-chain submit/rebalance after `DRY_RUN=false` |
| `DRY_RUN` | Keep `true` until you want txs |
| `MODEL_ID` | `eth-1-2-8h-v1` (must match the registry) |

Leave `MODEL_8H_URL` unset; the pickle is in the slug.

---

## 4. Order that actually links them

1. GitHub `main` is deployed on Heroku (you already did this). Confirm:
   - `https://argon-bd8888db5430.herokuapp.com/health`
   - `https://argon-bd8888db5430.herokuapp.com/pools`
   - `https://argon-bd8888db5430.herokuapp.com/forecasts/latest`
2. Create the Next.js app on Vercel (import `Wangsamuels/argon`, or a new app that vendors `apps/web/lib`).
3. Paste the **Vercel table** in §1. Deploy.
4. Copy the Vercel production URL.
5. Heroku **argon** → Config Vars → set `FRONTEND_ORIGIN` to that URL. Web dyno restarts itself.
6. In the live site: connect wallet, switch Arb vs RH from `/pools`, deposit. Portfolio poll: `GET /portfolio/0x…` every 10s.

If the browser console shows a CORS error, `FRONTEND_ORIGIN` does not exactly match the page origin (scheme + host, no path).

---

## 5. What the frontend calls (for the Vercel fetch layer)

| Method | Path | Poll |
|--------|------|------|
| GET | `/pools` | 60s — APR cards, user picks Arb or RH |
| GET | `/portfolio/{address}` | 10s — `totalUsd` |
| GET | `/forecasts/latest` | 30s |
| GET | `/status` | 30s |
| GET | `/vault` | TVL, no wallet |

Base = `NEXT_PUBLIC_AGENT_URL`. Client: `apps/web/lib/agent.ts`, `usePools.ts`, `usePortfolio.ts`.
