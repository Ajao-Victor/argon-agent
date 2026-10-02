import io, pickle, os, hashlib
from pathlib import Path
import pytest
from argon_agent import model_types, chain, tick_job

MODEL = Path(__file__).resolve().parent.parent / "models" / "eth_8h_lgbm.pkl"


def test_real_model_still_loads_under_allowlist():
    b = model_types.load_bundle(MODEL, hashlib.sha256(MODEL.read_bytes()).hexdigest())
    assert set(b) >= {"model", "scaler", "feature_columns"}


def test_hash_mismatch_refused():
    with pytest.raises(model_types.UnsafePickle):
        model_types.load_bundle(MODEL, "00" * 32)


def test_malicious_pickle_refused(tmp_path):
    class Evil:
        def __reduce__(self):
            return (os.system, ("echo pwned",))
    p = tmp_path / "evil.pkl"
    p.write_bytes(pickle.dumps(Evil()))
    with pytest.raises(model_types.UnsafePickle):
        model_types.load_bundle(p)


def test_spot_usd8_matches_solidity_math():
    weth = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"; usdc = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    v = chain.spot_usd8(4150123009056018256407751, weth, usdc, 6)
    assert 2_700e8 < v < 2_800e8


class FakeClient:
    def __init__(self, name, inpool):
        self.cfg = type("C", (), {"name": name})(); self._in = inpool; self.sent = None
    def submit(self, *a): return "0xsub"
    def in_pool(self): return self._in
    def rebalance(self, hour, action): self.sent = action; return "0xreb"


def test_action_is_per_chain():
    arb, rh = FakeClient("arbitrum", True), FakeClient("robinhood", False)
    chain.execute_hour([arb, rh], 1, 10, 20, 30, b"\0" * 32, 0, True)
    assert arb.sent == chain.HOLD and rh.sent == chain.ENTER_CODE


def test_failed_leg_marks_hour_incomplete(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    assert tick_job._chain_incomplete({"action": "exit", "rebalance_tx": "error:timeout"})
    assert not tick_job._chain_incomplete({"action": "exit", "rebalance_tx": "0xabc", "tx_hash": "already:5"})
    monkeypatch.setenv("DRY_RUN", "true")
    assert not tick_job._chain_incomplete({"action": "exit", "rebalance_tx": "error:x"})
