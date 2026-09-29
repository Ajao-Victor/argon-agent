from argon_agent.policy import ENTER, EXIT, HOLD, allowed_action, pct_to_bps


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
