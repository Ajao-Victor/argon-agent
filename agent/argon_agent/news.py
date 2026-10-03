"""High-impact US news calendar → keeper pause windows.

Rule: the vault leaves the pool on the hourly check one hour before the release's
UTC hour and may not re-enter until the check four hours after it.
  CPI 08:30 ET (12:30 UTC) → exit at 11:00 UTC, entries allowed again from 16:00 UTC.
  FOMC 14:00 ET decision / 14:30 press conference → exit 13:00 ET, resume 18:00 ET.

Sources, unioned so one bad source can only widen the pause, never drop it:
  1. Weekly economic-calendar feed (USD, impact High), cached in the DB so a feed outage
     keeps the events already seen.
  2. Built-in official dates (Fed FOMC calendar, BLS CPI and jobs-report schedules).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("argon.news")

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FEED_TIMEOUT_SECS = 10
LEAD_HOURS = 1  # exit on the check this many hours before the release hour
RESUME_HOURS = 4  # first check where entries are allowed again, after the release hour
SYNC_LOOKAHEAD_HOURS = 24  # push the next window on-chain once it starts within this many hours
MAX_WINDOW_HOURS = 24  # ArgonVault.MAX_NEWS_PAUSE_HOURS
MATCH_TOLERANCE_HOURS = 1
HORIZON_DAYS = 35  # how far ahead /news and /status look; on-chain scheduling stays at 24h

ET = ZoneInfo("America/New_York")

# Official schedules. FOMC: Fed calendar (decision 14:00 ET on day two).
# CPI and jobs report (NFP): BLS release schedules, 08:30 ET. BLS has not yet published 2027.
FOMC_DECISION_DAYS = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
    "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09",
    "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08",
]
CPI_DAYS = [
    "2026-01-13", "2026-02-13", "2026-03-11", "2026-04-10", "2026-05-12", "2026-06-10",
    "2026-07-14", "2026-08-12", "2026-09-11", "2026-10-14", "2026-11-10", "2026-12-10",
]
NFP_DAYS = [
    "2026-01-09", "2026-02-11", "2026-03-06", "2026-04-03", "2026-05-08", "2026-06-05",
    "2026-07-02", "2026-08-07", "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04",
]


@dataclass(frozen=True)
class NewsEvent:
    title: str
    at: datetime  # UTC
    source: str  # "feed" | "official"

    @property
    def hour_id(self) -> int:
        return int(self.at.timestamp()) // 3600

    @property
    def key(self) -> str:
        return f"{self.title}|{self.at.isoformat()}"


@dataclass
class PauseWindow:
    from_hour: int  # inclusive: first check that must exit
    until_hour: int  # exclusive: first check allowed to enter again
    events: list[NewsEvent] = field(default_factory=list)

    def contains(self, hour_id: int) -> bool:
        return self.from_hour <= hour_id < self.until_hour

    def to_api(self) -> dict:
        return {
            "fromHourId": self.from_hour,
            "untilHourId": self.until_hour,
            "exitAt": _hour_iso(self.from_hour),
            "resumesAt": _hour_iso(self.until_hour),
            "events": [{"title": e.title, "at": e.at.isoformat(), "source": e.source} for e in self.events],
        }


def _hour_iso(hour_id: int) -> str:
    return datetime.fromtimestamp(hour_id * 3600, tz=timezone.utc).isoformat()


def _et(day: str, hh: int, mm: int) -> datetime:
    local = datetime.combine(date.fromisoformat(day), time(hh, mm), tzinfo=ET)
    return local.astimezone(timezone.utc)


def official_events() -> list[NewsEvent]:
    out = [NewsEvent("FOMC rate decision", _et(d, 14, 0), "official") for d in FOMC_DECISION_DAYS]
    out += [NewsEvent("FOMC press conference", _et(d, 14, 30), "official") for d in FOMC_DECISION_DAYS]
    out += [NewsEvent("CPI", _et(d, 8, 30), "official") for d in CPI_DAYS]
    out += [NewsEvent("Non-Farm Payrolls", _et(d, 8, 30), "official") for d in NFP_DAYS]
    return out


def parse_feed(items: list[dict]) -> list[NewsEvent]:
    out: list[NewsEvent] = []
    for it in items:
        if str(it.get("country", "")).upper() != "USD":
            continue
        if str(it.get("impact", "")).lower() != "high":
            continue
        raw = it.get("date")
        try:
            at = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            log.warning("news feed: unparseable date %r for %r", raw, it.get("title"))
            continue
        if at.tzinfo is None:
            log.warning("news feed: date without timezone %r; skipping", raw)
            continue
        out.append(NewsEvent(str(it.get("title") or "USD high-impact"), at.astimezone(timezone.utc), "feed"))
    return out


def fetch_feed() -> list[NewsEvent] | None:
    """None when the feed is unreachable or malformed (callers fall back to cache + official)."""
    import requests

    try:
        r = requests.get(FEED_URL, timeout=FEED_TIMEOUT_SECS, headers={"User-Agent": "argon-keeper/1.0"})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            raise ValueError("feed is not a list")
        return parse_feed(data)
    except Exception as exc:
        log.warning("news feed fetch failed: %s", exc)
        return None


def cross_check(feed: list[NewsEvent], official: list[NewsEvent], now: datetime) -> list[str]:
    """Official events this feed week that the feed does not list near the same time."""
    if not feed:
        return []
    lo = min(e.at for e in feed) - timedelta(hours=12)
    hi = max(e.at for e in feed) + timedelta(hours=12)
    missing = []
    for o in official:
        if not (lo <= o.at <= hi):
            continue
        if not any(abs(f.hour_id - o.hour_id) <= MATCH_TOLERANCE_HOURS for f in feed):
            missing.append(f"{o.title} {o.at.isoformat()}")
    for m in missing:
        log.warning("news cross-check: official %s not in feed; keeping the official time", m)
    return missing


def merge_windows(events: list[NewsEvent]) -> list[PauseWindow]:
    spans = sorted(
        ((e.hour_id - LEAD_HOURS, e.hour_id + RESUME_HOURS, e) for e in events), key=lambda t: (t[0], t[1])
    )
    out: list[PauseWindow] = []
    for f, u, e in spans:
        if out and f <= out[-1].until_hour:
            out[-1].until_hour = max(out[-1].until_hour, u)
            if not any(x.key == e.key for x in out[-1].events):
                out[-1].events.append(e)
        else:
            out.append(PauseWindow(f, u, [e]))
    for w in out:
        w.events.sort(key=lambda e: e.at)
    return out


def _dedupe(events: list[NewsEvent]) -> list[NewsEvent]:
    seen: dict[tuple[str, int], NewsEvent] = {}
    for e in events:
        k = (e.title.lower(), e.hour_id)
        if k not in seen or (seen[k].source != "feed" and e.source == "feed"):
            seen[k] = e
    return list(seen.values())


def upcoming_events(store=None, *, refresh: bool, now: datetime | None = None) -> list[NewsEvent]:
    now = now or datetime.now(timezone.utc)
    official = official_events()
    feed: list[NewsEvent] = []
    if refresh:
        fetched = fetch_feed()
        if fetched is not None:
            cross_check(fetched, official, now)
            feed = fetched
            if store is not None:
                try:
                    store.save_news_events(fetched)
                except Exception:
                    log.exception("news cache write failed")
    if store is not None:
        try:
            feed = feed + store.list_news_events(now - timedelta(hours=12), now + timedelta(days=HORIZON_DAYS))
        except Exception:
            log.exception("news cache read failed")
    lo, hi = now - timedelta(hours=RESUME_HOURS + 2), now + timedelta(days=HORIZON_DAYS)
    return [e for e in _dedupe(feed + official) if lo <= e.at <= hi]


def windows(store=None, *, refresh: bool = False, now: datetime | None = None) -> list[PauseWindow]:
    return merge_windows(upcoming_events(store, refresh=refresh, now=now))


def active_window(wins: list[PauseWindow], hour_id: int) -> PauseWindow | None:
    return next((w for w in wins if w.contains(hour_id)), None)


def next_window(wins: list[PauseWindow], hour_id: int) -> PauseWindow | None:
    return next((w for w in wins if w.from_hour > hour_id), None)


def desired_onchain(wins: list[PauseWindow], hour_id: int) -> tuple[int, int] | None:
    """The (from, until) the vault should hold now: the active window, else the next one within lookahead."""
    w = active_window(wins, hour_id)
    if w is not None:
        start = w.from_hour
        if w.until_hour - start > MAX_WINDOW_HOURS:
            start = hour_id
        return start, min(w.until_hour, start + MAX_WINDOW_HOURS)
    w = next_window(wins, hour_id)
    if w is not None and w.from_hour - hour_id <= SYNC_LOOKAHEAD_HOURS:
        return w.from_hour, min(w.until_hour, w.from_hour + MAX_WINDOW_HOURS)
    return None


def status_payload(wins: list[PauseWindow], hour_id: int) -> dict:
    act = active_window(wins, hour_id)
    nxt = next_window(wins, hour_id)
    return {
        "active": act is not None,
        "current": act.to_api() if act else None,
        "next": nxt.to_api() if nxt else None,
        "rule": f"exit {LEAD_HOURS}h before the release hour; no entries until {RESUME_HOURS}h after it",
    }
