"""Postgres (Heroku) or SQLite (local) forecast store."""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from argon_agent.config import database_url

log = logging.getLogger("argon.db")

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS forecasts (
  hour_id             BIGINT PRIMARY KEY,
  target_hour_id      BIGINT NOT NULL,
  submitted_at        TIMESTAMPTZ NOT NULL,
  eth_pct_1h          DOUBLE PRECISION NOT NULL,
  eth_pct_2h          DOUBLE PRECISION NOT NULL,
  eth_pct_8h          DOUBLE PRECISION NOT NULL,
  eth_pct_1h_source   TEXT NOT NULL DEFAULT 'lgbm',
  eth_pct_2h_source   TEXT NOT NULL DEFAULT 'lgbm',
  eth_pct_8h_source   TEXT NOT NULL DEFAULT 'lgbm',
  spot_usd            DOUBLE PRECISION,
  model_id            TEXT NOT NULL,
  status              TEXT NOT NULL,
  realized_pct_change DOUBLE PRECISION,
  realized_spot_usd   DOUBLE PRECISION,
  action              TEXT NOT NULL,
  forecast_hash       TEXT,
  tx_hash             TEXT,
  tx_hash_rh          TEXT,
  rebalance_tx        TEXT,
  rebalance_tx_rh     TEXT,
  pool_status_arb     INTEGER,
  pool_status_rh      INTEGER,
  bar_time            TIMESTAMPTZ,
  bar_hour_id         BIGINT,
  pred_eth_usd_8h     DOUBLE PRECISION,
  bar_close_usd       DOUBLE PRECISION,
  expected_eth_usd_1h DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS forecasts_target_idx ON forecasts (target_hour_id);
CREATE TABLE IF NOT EXISTS signer_gates (
  address          TEXT PRIMARY KEY,
  preset           TEXT NOT NULL,
  top_1h_bps       INTEGER NOT NULL,
  bottom_1h_bps    INTEGER NOT NULL,
  top_2h_bps       INTEGER NOT NULL,
  bottom_2h_bps    INTEGER NOT NULL,
  top_8h_bps       INTEGER NOT NULL,
  bottom_8h_bps    INTEGER NOT NULL,
  signature        TEXT NOT NULL,
  signed_message   TEXT NOT NULL,
  issued_at        BIGINT NOT NULL,
  updated_at       TIMESTAMPTZ NOT NULL,
  in_position      INTEGER NOT NULL DEFAULT 0,
  last_action      TEXT,
  last_hour_id     BIGINT
);
"""

SQLITE_SCHEMA = PG_SCHEMA.replace("TIMESTAMPTZ", "TEXT").replace("DOUBLE PRECISION", "REAL")


class Store:
    def __init__(self) -> None:
        self.url = database_url()
        self.postgres = self.url is not None

    @contextmanager
    def conn(self) -> Iterator[Any]:
        if self.postgres:
            import psycopg2
            import psycopg2.extras

            c = psycopg2.connect(
                self.url,
                cursor_factory=psycopg2.extras.RealDictCursor,
                sslmode=os.getenv("PGSSLMODE", "prefer"),
            )
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise
            finally:
                c.close()
        else:
            path = "forecasts.db"
            c = sqlite3.connect(path)
            c.row_factory = sqlite3.Row
            try:
                yield c
                c.commit()
            finally:
                c.close()

    def _q(self, sql: str) -> str:
        if self.postgres:
            return sql
        return sql.replace("%s", "?")

    def ensure_schema(self) -> None:
        schema = PG_SCHEMA if self.postgres else SQLITE_SCHEMA
        bar_type = "TIMESTAMPTZ" if self.postgres else "TEXT"
        migrations = [
            f"ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS bar_time {bar_type}",
            "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS bar_hour_id BIGINT",
            "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS pred_eth_usd_8h DOUBLE PRECISION",
            "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS bar_close_usd DOUBLE PRECISION",
            "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS expected_eth_usd_1h DOUBLE PRECISION",
        ]
        with self.conn() as c:
            cur = c.cursor()
            for stmt in schema.split(";"):
                s = stmt.strip()
                if s:
                    cur.execute(s)
            for stmt in migrations:
                try:
                    cur.execute(stmt)
                except Exception:
                    log.debug("migration skipped: %s", stmt)

    def count(self) -> int:
        with self.conn() as c:
            cur = c.cursor()
            cur.execute("SELECT COUNT(*) FROM forecasts")
            row = cur.fetchone()
            return int(row[0] if not isinstance(row, dict) else list(row.values())[0])

    def get(self, hour_id: int) -> dict | None:
        with self.conn() as c:
            cur = c.cursor()
            cur.execute(self._q("SELECT * FROM forecasts WHERE hour_id = %s"), (hour_id,))
            return _row(cur)

    def latest(self) -> dict | None:
        with self.conn() as c:
            cur = c.cursor()
            cur.execute("SELECT * FROM forecasts ORDER BY hour_id DESC LIMIT 1")
            return _row(cur)

    def list_recent(self, limit: int = 24) -> list[dict]:
        with self.conn() as c:
            cur = c.cursor()
            cur.execute(self._q("SELECT * FROM forecasts ORDER BY hour_id DESC LIMIT %s"), (limit,))
            rows = cur.fetchall()
            return [_as_dict(r, cur) for r in rows]

    def pending(self) -> list[dict]:
        with self.conn() as c:
            cur = c.cursor()
            cur.execute("SELECT * FROM forecasts WHERE status = 'pending' ORDER BY hour_id")
            rows = cur.fetchall()
            return [_as_dict(r, cur) for r in rows]

    def upsert(self, row: dict) -> None:
        cols = list(row.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        colsql = ", ".join(cols)
        if self.postgres:
            updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != "hour_id")
            sql = (
                f"INSERT INTO forecasts ({colsql}) VALUES ({placeholders}) "
                f"ON CONFLICT (hour_id) DO UPDATE SET {updates}"
            )
        else:
            sql = f"INSERT OR REPLACE INTO forecasts ({colsql}) VALUES ({placeholders})"
        with self.conn() as c:
            cur = c.cursor()
            cur.execute(self._q(sql), tuple(row[c] for c in cols))

    def update(self, hour_id: int, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = %s" for k in fields)
        sql = f"UPDATE forecasts SET {sets} WHERE hour_id = %s"
        with self.conn() as c:
            cur = c.cursor()
            cur.execute(self._q(sql), tuple(fields.values()) + (hour_id,))

    def mature(self, now_hour: int, realized_spot: float | None) -> None:
        pending = self.pending()
        for row in pending:
            target = int(row["target_hour_id"])
            if now_hour < target:
                continue
            submitted_spot = row.get("spot_usd")
            realized_pct = None
            if submitted_spot and realized_spot:
                realized_pct = (float(realized_spot) / float(submitted_spot) - 1.0) * 100.0
            self.update(
                int(row["hour_id"]),
                status="matured",
                realized_pct_change=realized_pct,
                realized_spot_usd=realized_spot,
            )

    def get_gate(self, address: str) -> dict | None:
        with self.conn() as c:
            cur = c.cursor()
            cur.execute(self._q("SELECT * FROM signer_gates WHERE address = %s"), (address,))
            return _row(cur)

    def list_gates(self) -> list[dict]:
        with self.conn() as c:
            cur = c.cursor()
            cur.execute("SELECT * FROM signer_gates ORDER BY address")
            rows = cur.fetchall()
            return [_as_dict(r, cur) for r in rows]

    def save_gate(self, row: dict) -> None:
        address = row["address"]
        existing = self.get_gate(address)
        if existing and int(row["issued_at"]) <= int(existing["issued_at"]):
            raise ValueError("stale gate signature")
        cols = list(row.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        colsql = ", ".join(cols)
        if self.postgres:
            updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != "address")
            sql = (
                f"INSERT INTO signer_gates ({colsql}) VALUES ({placeholders}) "
                f"ON CONFLICT (address) DO UPDATE SET {updates}"
            )
        else:
            sql = f"INSERT OR REPLACE INTO signer_gates ({colsql}) VALUES ({placeholders})"
        with self.conn() as c:
            cur = c.cursor()
            cur.execute(self._q(sql), tuple(row[c] for c in cols))

    def save_gate_decisions(self, rows: list[tuple]) -> None:
        if not rows:
            return
        sql = "UPDATE signer_gates SET last_action = %s, last_hour_id = %s, in_position = %s WHERE address = %s"
        with self.conn() as c:
            cur = c.cursor()
            cur.executemany(self._q(sql), rows)


def _as_dict(row: Any, cur: Any) -> dict:
    if row is None:
        return {}
    if isinstance(row, sqlite3.Row):
        return dict(row)
    if isinstance(row, dict):
        return row
    names = [d[0] for d in cur.description]
    return dict(zip(names, row))


def _row(cur: Any) -> dict | None:
    row = cur.fetchone()
    if row is None:
        return None
    return _as_dict(row, cur)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
