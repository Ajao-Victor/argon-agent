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


class _Call:
    def __init__(self, v): self.v = v
    def call(self): return self.v


class FakeClient:
    """Mimics ChainClient plus the vault's news-pause state and setNewsPause rules."""

    def __init__(self, name, inpool, pause=(0, 0), sync_fails=False):
        self.cfg = type("C", (), {"name": name})(); self._in = inpool; self.sent = None
        self.pause = pause; self.sync_fails = sync_fails; self.synced = []
        fc = self
        self.vault = type("V", (), {"functions": type("F", (), {
            "newsPaused": staticmethod(lambda h: _Call(fc.pause[0] <= h < fc.pause[1])),
        })()})()
    def submit(self, *a): return "0xsub"
    def in_pool(self): return self._in
    def vault_gates(self): return 100, 250, 200
    def sync_news_pause(self, hour, desired):
        if desired is None or desired == self.pause:
            return None
        if self.sync_fails:
            raise RuntimeError("rpc down")
        self.pause = desired; self.synced.append(desired); return "0xpause"
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


def _win(f, u):
    from argon_agent.news import PauseWindow
    return [PauseWindow(f, u)]


def test_news_pause_synced_then_exits_on_calm_forecast():
    arb, rh = FakeClient("arbitrum", True), FakeClient("robinhood", False)
    res = chain.execute_hour([arb, rh], 100, 10, 20, 30, b"\0" * 32, 0, True, _win(100, 105))
    assert arb.synced == [(100, 105)] and rh.synced == [(100, 105)]
    assert arb.sent == chain.EXIT and rh.sent == chain.EXIT
    assert res["news_pause"]["arbitrum"] == "0xpause"


def test_upcoming_window_is_scheduled_but_trading_unchanged():
    arb = FakeClient("arbitrum", False)
    chain.execute_hour([arb], 90, 10, 20, 30, b"\0" * 32, 0, True, _win(100, 105))
    assert arb.synced == [(100, 105)]
    assert arb.sent == chain.ENTER_CODE


def test_pause_never_enters_when_vault_sync_failed():
    idle = FakeClient("arbitrum", False, sync_fails=True)
    res = chain.execute_hour([idle], 100, 10, 20, 30, b"\0" * 32, 0, True, _win(100, 105))
    assert idle.sent is None
    assert res["rebalance"]["arbitrum"].startswith("error:news pause not on-chain")
    assert tick_job._chain_incomplete({"action": "exit", "rebalance_tx": res["rebalance"]["arbitrum"]}) or os.getenv("DRY_RUN", "false") == "true"


def test_pause_sync_failure_still_allows_forecast_exit():
    inpool = FakeClient("arbitrum", True, sync_fails=True)
    chain.execute_hour([inpool], 100, 150, 20, 30, b"\0" * 32, 0, True, _win(100, 105))
    assert inpool.sent == chain.EXIT


def test_resume_after_window():
    arb = FakeClient("arbitrum", False, pause=(100, 105))
    chain.execute_hour([arb], 105, 10, 20, 30, b"\0" * 32, 0, True, _win(100, 105))
    assert arb.sent == chain.ENTER_CODE and arb.synced == []
