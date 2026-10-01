from datetime import datetime, timezone

import pandas as pd
import pytest

from argon_agent.bars import StaleBarError, append_bars, bar_hour_id, closed_bars_for_hour
from argon_agent.serialize import current_hour_id, row_to_api


def _hourly(start: str, end: str) -> pd.DataFrame:
    idx = pd.date_range(start, end, freq="h", tz="UTC")
    return pd.DataFrame({"Close": range(len(idx))}, index=idx)


def test_closed_bar_is_previous_hour_not_in_progress():
    hour = current_hour_id(datetime(2026, 10, 1, 16, 0, tzinfo=timezone.utc))
    ohlc = _hourly("2026-09-01 00:00", "2026-10-01 16:00")
    closed = closed_bars_for_hour(ohlc, hour)
    last = bar_hour_id(closed.index[-1])
    assert last == hour - 1
    assert last != hour


def test_rejects_previous_day_last_bar():
    hour = current_hour_id(datetime(2026, 10, 1, 16, 0, tzinfo=timezone.utc))
    ohlc = _hourly("2026-09-29 00:00", "2026-09-30 23:00")
    with pytest.raises(StaleBarError, match="previous-day"):
        closed_bars_for_hour(ohlc, hour)


def test_rejects_same_bar_carried_into_next_hour():
    # 10:00 UTC tick still seeing the 08:00 bar (Tiingo lag after a 09:55 boot).
    hour = current_hour_id(datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc))
    ohlc = _hourly("2026-09-29 00:00", "2026-09-30 08:00")
    with pytest.raises(StaleBarError):
        closed_bars_for_hour(ohlc, hour)


def test_appends_live_hours_when_tiingo_stops_midday():
    """The 22:00 tick failed because Tiingo's last bar was 15:00. Fill that gap."""
    hour = current_hour_id(datetime(2026, 10, 1, 22, 0, tzinfo=timezone.utc))
    tiingo = _hourly("2026-09-01 00:00", "2026-10-01 15:00")
    tiingo["High"] = tiingo["Close"]
    tiingo["Low"] = tiingo["Close"]
    tiingo["Open"] = tiingo["Close"]
    tiingo["Volume"] = 1.0
    live = _hourly("2026-10-01 16:00", "2026-10-01 21:00")
    live["High"] = live["Close"]
    live["Low"] = live["Close"]
    live["Open"] = live["Close"]
    live["Volume"] = 1.0
    merged = append_bars(tiingo, live)
    closed = closed_bars_for_hour(merged, hour)
    assert bar_hour_id(closed.index[-1]) == hour - 1


def test_api_marks_prior_hour_not_live():
    row = {
        "hour_id": 497434,
        "target_hour_id": 497442,
        "submitted_at": "2026-09-30T10:00:12+00:00",
        "eth_pct_1h": -0.0095,
        "eth_pct_2h": -0.132,
        "eth_pct_8h": -0.386,
        "eth_pct_1h_source": "persistence",
        "eth_pct_2h_source": "persistence",
        "eth_pct_8h_source": "lgbm",
        "spot_usd": 2688.0,
        "model_id": "eth-1-2-8h-v1",
        "status": "pending",
        "realized_pct_change": None,
        "realized_spot_usd": None,
        "action": "warmup",
        "forecast_hash": "0xabc",
        "tx_hash": None,
        "tx_hash_rh": None,
        "rebalance_tx": None,
        "rebalance_tx_rh": None,
        "pool_status_arb": 0,
        "pool_status_rh": 0,
        "bar_time": "2026-09-30T08:00:00+00:00",
        "bar_hour_id": 497432,
    }
    payload = row_to_api(row, warmup_complete=False, now_hour=497464)
    assert payload["live"] is False
    assert payload["barHourId"] == 497432
    assert payload["hourId"] != 497464
