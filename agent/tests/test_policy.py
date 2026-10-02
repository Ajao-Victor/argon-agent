from argon_agent.policy import (
    ENTER,
    EXIT,
    HOLD,
    allowed_action,
    catchup_pct,
    path_expected_price,
    pct_from_prices,
    pct_to_bps,
    pred_price_from_log_return,
)


def test_pct_to_bps():
    assert pct_to_bps(-0.40) == -40
    assert pct_to_bps(-2.41) == -241
    assert pct_to_bps(1.0) == 100


def test_enter_when_all_inside_and_idle():
    assert allowed_action(-40, -110, -150, in_pool=False) == ENTER


def test_exit_when_2h_outside_even_if_1h_small():
    assert allowed_action(-30, -280, -150, in_pool=True) == EXIT


def test_exit_when_1h_outside():
    assert allowed_action(120, 10, 10, in_pool=True) == EXIT


def test_idle_stay_flat_when_8h_outside():
    assert allowed_action(-20, -30, -250, in_pool=False) == EXIT


def test_hold_when_in_pool_and_inside():
    assert allowed_action(-20, -30, -50, in_pool=True) == HOLD


def test_warmup_is_hold():
    assert allowed_action(-20, -30, -50, in_pool=False, warmup_complete=False) == HOLD


def test_8h_log_return_becomes_price_not_live_spot():
    import math

    close = 2600.0
    log_ret = 0.01
    pred = pred_price_from_log_return(close, log_ret)
    assert abs(pred - close * math.exp(log_ret)) < 1e-9
    assert abs(pct_from_prices(close, pred) - (math.exp(log_ret) - 1.0) * 100.0) < 1e-9


def test_first_hour_path_is_the_predicted_price():
    start, target = 2685.0, 2695.72
    assert path_expected_price(start, target, 0) == start
    assert abs(path_expected_price(start, target, 8) - target) < 1e-9


def test_next_hour_catchup_vs_previously_submitted_price():
    start, target = 100.0, 108.0
    expected_1h = path_expected_price(start, target, 1)
    assert expected_1h > start
    assert expected_1h < target
    assert abs(catchup_pct(start, target, expected_1h, 1)) < 1e-9
    ahead = catchup_pct(start, target, expected_1h * 1.02, 1)
    assert ahead > 0


def test_one_hour_slice_is_the_average_of_open_eight_hour_targets():
    from argon_agent.policy import average_open_slice, implied_slice_pct

    current = 2700.0
    # Issued this hour: $2754 in 8h. Issued 7 hours ago: $2740 with 1h left.
    calls = [(2754.0, 8), (2740.0, 1)]
    fresh = implied_slice_pct(2754.0, current, 8, 1)
    last = implied_slice_pct(2740.0, current, 1, 1)
    averaged = average_open_slice(calls, current, 1)
    assert averaged is not None
    assert abs(averaged - (fresh + last) / 2) < 1e-9
    # The matured target with 0 hours left is not part of the 1h average.
    assert average_open_slice([(2754.0, 0)], current, 1) is None
    two_hour = average_open_slice(calls, current, 2)
    assert two_hour is not None
    assert abs(two_hour - implied_slice_pct(2754.0, current, 8, 2)) < 1e-9


def test_custom_gate_exit_when_price_is_above_the_user_top():
    from argon_agent.policy import resolve_gate, user_action

    gate = resolve_gate("custom", 80, -300)
    action, in_position = user_action(120, 50, 40, gate, in_position=True, warmup_complete=True)
    assert action == "exit"
    assert in_position is False
    safe = resolve_gate("safe", 60, -60)
    assert safe["top_1h_bps"] == 60
    assert safe["top_2h_bps"] == 120
