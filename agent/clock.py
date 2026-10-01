"""Heroku clock dyno: infer on boot, then every UTC hour."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from argon_agent.tick_job import run_hour

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("argon.clock")


def seconds_until_next_hour() -> float:
    now = datetime.now(timezone.utc)
    elapsed = now.minute * 60 + now.second + now.microsecond / 1e6
    return max(5.0, 3600.0 - elapsed + 25.0)


def main() -> None:
    log.info("clock starting — running first tick immediately")
    try:
        run_hour()
    except Exception:
        log.exception("boot tick failed")
    while True:
        wait = seconds_until_next_hour()
        log.info("sleeping %.0fs until next UTC hour", wait)
        time.sleep(wait)
        try:
            run_hour()
        except Exception:
            log.exception("hourly tick failed")


if __name__ == "__main__":
    main()
