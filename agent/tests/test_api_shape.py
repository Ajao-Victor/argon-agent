from argon_agent.serialize import current_hour_id, row_to_api


def test_hour_id_epoch():
    from datetime import datetime, timezone

    d = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert current_hour_id(d) == int(d.timestamp() // 3600)


def test_api_shape():
    row = {
        "hour_id": 1,
        "target_hour_id": 9,
        "submitted_at": "2026-01-01T00:00:00+00:00",
        "eth_pct_1h": -0.4,
        "eth_pct_2h": -1.1,
        "eth_pct_8h": -1.5,
        "eth_pct_1h_source": "persistence",
        "eth_pct_2h_source": "persistence",
        "eth_pct_8h_source": "lgbm",
        "spot_usd": 2410.12,
        "pred_eth_usd_8h": 2374.0,
        "bar_close_usd": 2410.12,
        "model_id": "eth-1-2-8h-v1",
        "status": "pending",
        "realized_pct_change": None,
        "realized_spot_usd": None,
        "action": "enter",
        "forecast_hash": "0xabc",
        "tx_hash": "0xdef",
        "tx_hash_rh": None,
        "rebalance_tx": None,
        "rebalance_tx_rh": None,
        "pool_status_arb": 1,
        "pool_status_rh": 0,
    }
    payload = row_to_api(row, warmup_complete=True, now_hour=1)
    assert payload["hourId"] == 1
    assert payload["ethPct8h"] == -1.5
    assert payload["action"] == "enter"
    assert payload["warmupComplete"] is True
    assert payload["forecastHash"] == "0xabc"
    assert payload["live"] is True
    assert payload["barHourId"] is None
    assert payload["predEthUsd8h"] == 2374.0
    assert payload["barCloseUsd"] == 2410.12
