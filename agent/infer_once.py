"""Run one infer + store + keeper cycle. Used by Heroku Scheduler or locally."""

from __future__ import annotations

import json
import logging

from argon_agent.tick_job import run_hour

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

if __name__ == "__main__":
    print(json.dumps(run_hour(), indent=2, default=str))
