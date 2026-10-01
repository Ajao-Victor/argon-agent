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
    log.info("clock starting — inferring this UTC hour, then retrying until it is stored")
    while True:
        try:
            run_hour()
        except Exception:
            log.exception("tick failed — retrying this UTC hour in 60s")
            time.sleep(60)
            continue
        wait = seconds_until_next_hour()
        log.info("hour stored, sleeping %.0fs until next UTC hour", wait)
        time.sleep(wait)


if __name__ == "__main__":
    main()
