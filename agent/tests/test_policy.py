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
