# Argon — Heroku model deploy and forecast storage

The generic “wrap `.h5` in FastAPI and `git push heroku`” guide is **necessary but not sufficient**. It gets you a process that can load Keras. It does **not** deploy Argon.

## Does that 6-step guide cover us?

| That guide | Argon |
|------------|--------|
| `POST /predict` with a raw `features` array | The **clock** builds features (Tiingo/DIA + scaler) and infers. The website only `GET`s stored forecasts. |
| Load `model.h5` on the web dyno | Same, plus **scaler** / feature column list. Do not pickle the whole pipeline with API keys. |
| `tensorflow-cpu` because of the 1 GB slug | Still do this. If the slug is too big, move `model.h5` to S3/GCS and download at boot. |
| `web: uvicorn ... $PORT` | Keep **web** for GET APIs. Add a **clock** (or Heroku Scheduler) or hourly infer will only run when someone hits the site. Eco dynos sleep. |
| No database | **Required.** Dyno filesystem is wiped on restart. The 8h window cannot live in a list in `app.py`. |
| No `hourId` / dual-horizon gate / warmup | Policy and storage live next to infer, not in the dApp. |

This repo uses a **LightGBM pickle** (`eth_8h_lgbm.pkl`), not Keras. Pin versions in `agent/requirements.txt`. The working app is `agent/` — see [`agent/README.md`](../agent/README.md) for the Procfile, schema, keeper, and Heroku + Vercel steps.

Do **not** `git init` inside `contracts/`. Push `agent/` with `git subtree split --prefix=agent` (or a dedicated Heroku git remote whose root is `agent/`).

---

## Where the “9h window” is stored

**Horizon is 8 hours. The first trade is at clock hour 8.**  
Counting the infer at hour 0 **through** hour 8 is **9 rows**. That is the warmup window: 8 pending forecasts plus the infer that runs at the first decision hour.

| Clock hour | Rows in DB | Matured | Decision |
|------------|------------|---------|----------|
| 0 | 1 pending (targets hour 8) | 0 | none |
| 7 | 8 pending (target hours 8–15) | 0 | none |
| 8 | 8 pending + 1 matured (hour 0) + new pending (targets 16) | 1 | **first** ENTER/EXIT |
| 9+ | always 8 pending; matured pile grows | 2, 3, … | every hour |

**Store this in Heroku Postgres**, not on disk, not in RAM.

- RAM (`dict` / `deque`): gone on restart, dyno cycle, or deploy.  
- Local `forecasts.json` / SQLite on the dyno: same. Ephemeral.  
- Redis: OK as a cache, bad as the only store (you need matured predicted vs realized for the UI).  
- **Postgres (`heroku addons:create heroku-postgresql:essential-0`)** — source of truth.

### Table

```sql
CREATE TABLE forecasts (
  hour_id            BIGINT PRIMARY KEY,   -- floor(unix/3600) when we inferred
  target_hour_id     BIGINT NOT NULL,      -- hour_id + 8
  submitted_at       TIMESTAMPTZ NOT NULL,
  eth_pct_1h         DOUBLE PRECISION NOT NULL,
  eth_pct_2h         DOUBLE PRECISION NOT NULL,
  eth_pct_8h         DOUBLE PRECISION NOT NULL,
  spot_usd           DOUBLE PRECISION,
  model_id           TEXT NOT NULL,
  status             TEXT NOT NULL,        -- pending | matured
  realized_pct_change DOUBLE PRECISION,    -- filled when now_hour >= target_hour_id
  realized_spot_usd  DOUBLE PRECISION,
  action             TEXT NOT NULL,        -- warmup | exit | enter | hold
  forecast_hash      TEXT,
  tx_hash            TEXT
);

CREATE INDEX forecasts_target_idx ON forecasts (target_hour_id);
```

Keep **all** rows (or last 30 days). The “window” is a query, not a ring buffer you have to size at 9:

```sql
-- pending 8h queue (should be 8 rows after warmup)
SELECT * FROM forecasts
WHERE status = 'pending'
ORDER BY hour_id DESC;

-- first decision exists?
SELECT COUNT(*) >= 9 AS warmup_complete FROM forecasts;
-- or: MAX(hour_id) - MIN(hour_id) >= 8
```

At each hourly tick:

1. Load `.h5` (already in memory from boot).  
2. Build the current feature vector (not a payload from the website).  
3. `predict` → `eth_pct_1h`, `eth_pct_2h`, `eth_pct_8h`.  
4. `INSERT` `hour_id` plus the three percents, `status = pending`.  
5. `UPDATE` matured realized columns when `now_hour` hits +1 / +2 / +8.  
6. If `COUNT(*) >= 9`, set `action` from the dual-horizon gate and optionally keeper `submit` / `rebalance`.

The website only reads this table via `GET /forecasts/latest` and `GET /forecasts?limit=24`. It never writes the window.

---

## Procfile Argon actually needs

```
web: uvicorn app:app --host 0.0.0.0 --port $PORT
clock: python clock.py
```

`clock.py` sleeps until the next UTC hour, then runs infer + SQL. Alternative: Heroku Scheduler addon, `0 * * * * python infer_once.py`.

`app.py` exposes GET `/health`, `/status`, `/forecasts/latest`, `/forecasts`. No public `POST /predict` unless you protect it; the dApp must not send raw features.

---

## Minimal directory (agent app)

```
agent/
  app.py              # FastAPI reads Postgres
  clock.py            # hourly infer + insert/update
  infer.py            # load h5 + scaler, return eth_pct_1h/2h/8h
  model.h5            # or download from S3 at boot
  scaler.pkl          # whatever you fit at train time
  requirements.txt    # fastapi uvicorn numpy tensorflow-cpu psycopg2-binary
  Procfile
  runtime.txt         # python-3.11.x
```

Heroku config vars: `DATABASE_URL` (injected by Postgres), `TIINGO_API_KEY`, `GATE_1H_BPS=100`, `GATE_2H_BPS=250`, `GATE_8H_BPS=200`. Not in git.

---

## Deploy sketch (existing git repo)

From the agent folder or monorepo subtree:

```bash
heroku create argon-agent
heroku addons:create heroku-postgresql:essential-0
heroku config:set TIINGO_API_KEY=... GATE_BPS=200
git push heroku HEAD:main
heroku ps:scale web=1 clock=1
heroku logs --tail
```

Then point the dApp `NEXT_PUBLIC_AGENT_URL` at `https://argon-agent-….herokuapp.com`.
