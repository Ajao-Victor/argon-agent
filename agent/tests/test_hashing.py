from argon_agent.hashing import forecast_hash, hex_hash, model_id_bytes
from argon_agent.ticks import nearest_usable_tick, range_around


def test_model_id_is_keccak_of_text():
    mid = model_id_bytes("eth-1-2-8h-v1")
    assert len(mid) == 32
    assert hex_hash(mid).startswith("0x")


def test_forecast_hash_stable():
    a = forecast_hash(488888, -40, -110, -150)
    b = forecast_hash(488888, -40, -110, -150)
    c = forecast_hash(488888, -40, -110, -151)
    assert a == b
    assert a != c
    assert len(a) == 32


def test_tick_alignment():
    assert nearest_usable_tick(137, 10) == 140
    lower, upper = range_around(137, 10, half_width=200)
    assert lower % 10 == 0
    assert upper % 10 == 0
    assert lower < upper
