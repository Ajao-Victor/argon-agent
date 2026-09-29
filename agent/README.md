# Argon agent

Hourly ETH forecast service. This is the **only** process that loads `eth_8h_lgbm.pkl`, talks to Tiingo, writes Postgres, and signs keeper txs. The Vercel site is read-only against this API. User wallets never call `submit` or `rebalance`.

Live contracts (same addresses on Arbitrum `42161` and Robinhood `4663`):

| Contract | Address |
|----------|---------|
| InferenceRegistry | `0xbAf00c0aCa440337d43495c7de661A0AC2E01e8f` |
| ArgonVault | `0x9F844b4D1b28Be7413067f9d4fC08Bc276fd1C60` |
| UniswapV3Adapter | `0xECCc4B8946D0DB206f977d3021544D0cD5Dc69D4` |

Keeper / owner on both chains: `0x9642b6D1Db5D1A3B0A61a831099568bbCbC04D4E`.

```
Tiingo + DIA ──► infer.py (8h LightGBM pickle)
                      │
                      ▼
              dual-horizon gate (same math as DualHorizonGate.sol)
                      │
          ┌───────────┴────────────┐
          ▼                        ▼
   Postgres ──GET──► Vercel     keeper EOA
   /forecasts                    submit(hourId, 1h, 2h, 8h, hash)
                                 rebalance(pool 1 Arb, pool 4 RH)
```

## What you must add

These are **not** in git:

1. **`models/eth_8h_lgbm.pkl`** — the Colab pickle you already downloaded. Copy it here, or set `MODEL_8H_URL` to a public/signed HTTPS file the dyno can download at boot.
2. **`TIINGO_API_KEY`** — same key that trained the model. The agent sends `Authorization: Token <key>`.
3. **`KEEPER_PRIVATE_KEY`** — the EOA already set as `keeper` on the registry and vault. Fund it with ETH on **both** Arbitrum and Robinhood for gas.
4. **`FRONTEND_ORIGIN`** — your Vercel production URL (preview `*.vercel.app` is already allowed).

Optional later: `eth_1h_lgbm.pkl` and `eth_2h_lgbm.pkl` trained the same way as the 8h head. Until those exist, 1h and 2h are **persistence nowcasts** (last 1h / 2h realized ETH %). That is *not* `pred_8h / 8`. The 8h number always comes from LightGBM.

## Local run

```bash
cd agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: TIINGO_API_KEY, copy pickle to models/eth_8h_lgbm.pkl
# leave DRY_RUN=true until you want on-chain txs

PYTHONPATH=. python infer_once.py          # one tick → sqlite forecasts.db
PYTHONPATH=. uvicorn app:app --reload --port 8000
# GET http://127.0.0.1:8000/forecasts/latest
PYTHONPATH=. pytest
```

With `DRY_RUN=true` the API and DB still update; `submit` / `rebalance` are skipped.

## Hourly loop (what the clock does)

Every UTC hour (`clock.py`):

1. Fetch ~60 days of hourly ETH from Tiingo; DIA for spot.
2. Rebuild the training feature set; run the 8h pickle → `ethPct8h`.
3. Fill `ethPct1h` / `ethPct2h` from dedicated pickles or persistence.
4. Convert to signed bps (`-1.50%` → `-150`).
5. `forecastHash = keccak256(abi.encode(hourId, pct1h, pct2h, pct8h, keccak256("eth-1-2-8h-v1")))`.
6. Insert Postgres. Mark matured rows when `now_hour >= hour_id + 8`.
7. Gate (identical to Solidity):
   - hours 0–7 (fewer than 9 rows): `warmup` — **submit only**, no enter/exit.
   - `EXIT` if `|1h| ≥ 1%` **or** `|2h| ≥ 2.5%`.
   - `ENTER` if idle and all three inside (`|8h| < 2%` as well).
   - `HOLD` if already in pool and not EXIT.
8. Keeper `registry.submit(...)` then `vault.rebalance(hourId, poolId, action, ticks, 0, 0)`:
   - Arbitrum `poolId = 1` (WETH/USDC 500)
   - Robinhood `poolId = 4` (WETH/USDG 500)
9. The vault **re-checks** the gate on-chain. If the keeper passes ENTER when the stored bps say EXIT, the tx reverts.

The website never triggers this. It only `GET`s stored rows.

## REST the frontend calls

Base URL: `https://<app>.herokuapp.com` → `NEXT_PUBLIC_AGENT_URL`.

| Method | Path | Use |
|--------|------|-----|
| `GET` | `/health` | Agent up, pickle present |
| `GET` | `/status` | Warmup, last hour, gates |
| `GET` | `/forecasts/latest` | Dashboard hero |
| `GET` | `/forecasts?limit=24` | History |
| `GET` | `/forecasts/:hourId` | Predicted vs realized |

There is **no** public `POST /predict`. CORS is locked to `FRONTEND_ORIGIN` plus `https://*.vercel.app`.

`GET /forecasts/latest` shape:

```json
{
  "hourId": 488888,
  "targetHourId": 488896,
  "submittedAt": "2026-09-29T11:00:00+00:00",
  "ethPct1h": -0.40,
  "ethPct2h": -1.10,
  "ethPct8h": -1.50,
  "ethPct8hSource": "lgbm",
  "spotUsd": 2410.12,
  "modelId": "eth-1-2-8h-v1",
  "status": "pending",
  "action": "enter",
  "gate1hBps": 100,
  "gate2hBps": 250,
  "gate8hBps": 200,
  "warmupComplete": true,
  "forecastHash": "0x…",
  "txHash": "0x…",
  "trippedHorizons": []
}
```

The dashboard must show `forecastHash` next to `InferenceRegistry.getForecast(hourId)`. They have to match.

Frontend glue already lives in this monorepo:

- `apps/web/lib/agent.ts` — fetch helpers
- `apps/web/lib/policy.ts` — same gate (display only)
- `apps/web/lib/chains.ts` — Arb + Robinhood + live addresses
- `apps/web/lib/abis.ts` — deposit / withdraw / registry reads
- `apps/web/.env.example` — Vercel env vars

Scaffold Next.js + wagmi against those files. Users only sign `approve` + `deposit` / `withdraw`. Hide `rebalance`.

## Host on Heroku

Heroku needs this **folder as git root** (Procfile at the top of the slug). From the Argon monorepo:

```bash
# one-time
brew install heroku/brew/heroku   # or https://devcenter.heroku.com/articles/heroku-cli
heroku login

heroku create argon-agent
heroku buildpacks:add https://github.com/heroku/heroku-buildpack-apt
heroku buildpacks:add heroku/python
heroku addons:create heroku-postgresql:essential-0

heroku config:set \
  TIINGO_API_KEY=your_tiingo_token \
  KEEPER_PRIVATE_KEY=0x... \
  FRONTEND_ORIGIN=https://your-app.vercel.app \
  DRY_RUN=true \
  MODEL_ID=eth-1-2-8h-v1 \
  MODEL_8H_URL=https://…/eth_8h_lgbm.pkl

# deploy only agent/
git subtree split --prefix=agent -b heroku-agent
git push heroku heroku-agent:main

# or, if you prefer a dedicated clone:
# cd agent && git init && git add . && git commit -m "agent" && heroku git:remote -a argon-agent && git push heroku main

heroku ps:scale web=1 clock=1
heroku logs --tail
```

`web` serves GET APIs. `clock` sleeps until the next UTC hour and runs infer. Eco dynos sleep if you only scale `web` — **you need `clock=1`** or the hourly job never runs.

If you put the pickle in `agent/models/eth_8h_lgbm.pkl` instead of `MODEL_8H_URL`, force-add it (it is gitignored):

```bash
git add -f agent/models/eth_8h_lgbm.pkl
```

Go-live checklist:

1. `curl https://argon-agent-….herokuapp.com/health` → `"modelLoaded": true`
2. `curl …/forecasts/latest` → three percents + `forecastHash`
3. Flip `DRY_RUN=false` only after a dry tick looks right.
4. Confirm the keeper address on-chain is this key: `cast call $REGISTRY "keeper()(address)" --rpc-url $ARB_RPC`
5. Fund the keeper with a few dollars of ETH on Arb **and** Robinhood.
6. After 9 successful submits, `warmupComplete` becomes true and `rebalance` can ENTER/EXIT.

Heroku Scheduler (`0 * * * * python infer_once.py`) can replace the clock dyno if you want to save a process.

## Link the Vercel frontend

1. Create the Next.js app (or continue `apps/web`) with wagmi + the files above.
2. In the Vercel project: **Settings → Environment Variables**

```
NEXT_PUBLIC_AGENT_URL=https://argon-agent-XXXX.herokuapp.com
NEXT_PUBLIC_ARB_RPC=https://arb1.arbitrum.io/rpc
NEXT_PUBLIC_RH_RPC=https://rpc.mainnet.chain.robinhood.com
NEXT_PUBLIC_VAULT_ARB=0x9F844b4D1b28Be7413067f9d4fC08Bc276fd1C60
NEXT_PUBLIC_REGISTRY_ARB=0xbAf00c0aCa440337d43495c7de661A0AC2E01e8f
NEXT_PUBLIC_VAULT_RH=0x9F844b4D1b28Be7413067f9d4fC08Bc276fd1C60
NEXT_PUBLIC_REGISTRY_RH=0xbAf00c0aCa440337d43495c7de661A0AC2E01e8f
NEXT_PUBLIC_WALLETCONNECT_ID=
```

3. Deploy. Copy the production URL.
4. `heroku config:set FRONTEND_ORIGIN=https://your-app.vercel.app`
5. Poll `/forecasts/latest` every 30–60s (and around `:01` UTC). If Heroku is down, show the last on-chain `getForecast` and a banner — do not invent a %.

Do **not** put `KEEPER_PRIVATE_KEY` or `TIINGO_API_KEY` in Vercel.

## Layout

```
agent/
  app.py                 FastAPI GET /health /status /forecasts
  clock.py               UTC-hour loop
  infer_once.py          one-shot (Scheduler / local)
  Procfile               web + clock
  requirements.txt
  argon_agent/
    infer.py             pickle + features
    policy.py            DualHorizonGate
    tick_job.py          infer → db → submit → rebalance
    chain.py             web3 keeper
    db.py                Postgres / sqlite
  models/                eth_8h_lgbm.pkl (you add this)
apps/web/lib/            Vercel client + ABIs
```

## Safety

- The keeper can only call `submit` and `rebalance`. It cannot withdraw user funds to an arbitrary address.
- The vault re-runs the gate; a buggy agent cannot ENTER into an EXIT signal.
- Pause is owner-only on the vault. Idle (and flatten-then-pro-rata) withdraw stays available to users.
- `DRY_RUN=true` is the default in `app.json` so a first push cannot spend gas until you unset it.
