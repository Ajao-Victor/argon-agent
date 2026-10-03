from datetime import datetime, timezone

from argon_agent import news
from argon_agent.news import NewsEvent, PauseWindow
from argon_agent.policy import ENTER, EXIT, HOLD, allowed_action, user_action


def H(y, m, d, hh, mm=0):
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp()) // 3600


def ev(y, m, d, hh, mm, title="CPI", source="official"):
    return NewsEvent(title, datetime(y, m, d, hh, mm, tzinfo=timezone.utc), source)


def test_cpi_window_exits_one_hour_before_and_resumes_four_after():
    # CPI 2026-10-14 08:30 ET = 12:30 UTC
    (w,) = news.merge_windows([ev(2026, 10, 14, 12, 30)])
    assert w.from_hour == H(2026, 10, 14, 11)
    assert w.until_hour == H(2026, 10, 14, 16)
    assert not w.contains(H(2026, 10, 14, 10))
    assert all(w.contains(H(2026, 10, 14, h)) for h in range(11, 16))
    assert not w.contains(H(2026, 10, 14, 16))


def test_fomc_matches_user_rule_exit_1pm_resume_6pm_et():
    fomc = [e for e in news.official_events() if e.at.date().isoformat() == "2026-10-28"]
    assert {e.title for e in fomc} == {"FOMC rate decision", "FOMC press conference"}
    (w,) = news.merge_windows(fomc)
    # 14:00 ET = 18:00 UTC on Oct 28 (EDT). Exit 13:00 ET (17 UTC), resume 18:00 ET (22 UTC).
    assert w.from_hour == H(2026, 10, 28, 17)
    assert w.until_hour == H(2026, 10, 28, 22)


def test_official_times_follow_us_daylight_saving():
    cpi_dec = next(e for e in news.official_events() if e.title == "CPI" and e.at.month == 12)
    assert (cpi_dec.at.hour, cpi_dec.at.minute) == (13, 30)  # EST: 08:30 ET = 13:30 UTC
    cpi_oct = next(e for e in news.official_events() if e.title == "CPI" and e.at.month == 10)
    assert (cpi_oct.at.hour, cpi_oct.at.minute) == (12, 30)  # EDT


def test_overlapping_events_merge_into_one_window():
    wins = news.merge_windows([ev(2026, 10, 2, 12, 30, "NFP"), ev(2026, 10, 2, 14, 0, "ISM")])
    assert len(wins) == 1
    assert wins[0].from_hour == H(2026, 10, 2, 11) and wins[0].until_hour == H(2026, 10, 2, 18)
    assert [e.title for e in wins[0].events] == ["NFP", "ISM"]


def test_separate_days_stay_separate():
    wins = news.merge_windows([ev(2026, 10, 2, 12, 30), ev(2026, 10, 14, 12, 30)])
    assert len(wins) == 2


def test_parse_feed_filters_usd_high_and_converts_to_utc():
    items = [
        {"title": "CPI m/m", "country": "USD", "date": "2026-10-14T08:30:00-04:00", "impact": "High"},
        {"title": "Retail", "country": "USD", "date": "2026-10-14T08:30:00-04:00", "impact": "Medium"},
        {"title": "ECB", "country": "EUR", "date": "2026-10-14T08:30:00-04:00", "impact": "High"},
        {"title": "Bad", "country": "USD", "date": "nope", "impact": "High"},
        {"title": "Naive", "country": "USD", "date": "2026-10-14T08:30:00", "impact": "High"},
    ]
    out = news.parse_feed(items)
    assert [e.title for e in out] == ["CPI m/m"]
    assert out[0].at == datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)


def test_cross_check_flags_official_event_missing_from_feed():
    feed = [ev(2026, 10, 12, 14, 0, "Something", "feed"), ev(2026, 10, 16, 14, 0, "Other", "feed")]
    missing = news.cross_check(feed, news.official_events(), datetime(2026, 10, 12, tzinfo=timezone.utc))
    assert any(m.startswith("CPI") for m in missing)


def test_feed_outage_still_pauses_on_official_dates(monkeypatch):
    monkeypatch.setattr(news, "fetch_feed", lambda: None)
    now = datetime(2026, 10, 14, 11, 5, tzinfo=timezone.utc)
    wins = news.windows(None, refresh=True, now=now)
    assert news.active_window(wins, H(2026, 10, 14, 11)) is not None


def test_desired_onchain_lookahead_and_cap():
    w = PauseWindow(100, 105)
    assert news.desired_onchain([w], 70) is None  # more than 24h ahead
    assert news.desired_onchain([w], 80) == (100, 105)
    assert news.desired_onchain([w], 102) == (100, 105)
    assert news.desired_onchain([w], 105) is None
    long = PauseWindow(100, 140)
    assert news.desired_onchain([long], 90) == (100, 124)
    assert news.desired_onchain([long], 120) == (120, 140)  # extend from now, never past 24h


def test_policy_pause_forces_exit_and_blocks_enter():
    calm = (-10, -20, -30)
    assert allowed_action(*calm, in_pool=False) == ENTER
    assert allowed_action(*calm, in_pool=False, news_paused=True) == EXIT
    assert allowed_action(*calm, in_pool=True) == HOLD
    assert allowed_action(*calm, in_pool=True, news_paused=True) == EXIT
    assert allowed_action(*calm, in_pool=True, news_paused=True, warmup_complete=False) == HOLD


def test_signer_gate_pause():
    gate = {k: v for k in ("1h", "2h", "8h") for k, v in ((f"top_{k}_bps", 500), (f"bottom_{k}_bps", -500))}
    assert user_action(0, 0, 0, gate, in_position=True, warmup_complete=True) == ("hold", True)
    assert user_action(0, 0, 0, gate, in_position=True, warmup_complete=True, news_paused=True) == ("exit", False)
