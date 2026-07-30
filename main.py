"""Entry point for the Amazon Price Error Monitor.

Usage:
    python main.py            # run the monitor forever
    python main.py --once     # run a single scan cycle and exit
"""
from __future__ import annotations

import argparse
import logging
import sys

from config import Settings, load_settings


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def validate(s: Settings) -> list[str]:
    problems: list[str] = []
    if not s.keepa_api_key:
        problems.append("KEEPA_API_KEY is missing (needed to run the monitor).")
    if not s.tiers:
        problems.append("No Discord webhooks configured (set DISCORD_WEBHOOK_50/60/70/80).")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Amazon Price Error Monitor")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    args = parser.parse_args()

    s = load_settings()
    setup_logging(s.log_level)
    log = logging.getLogger("apem")

    problems = validate(s)
    if problems:
        for p in problems:
            log.error(p)
        log.error("Fix the above in your .env (copy from .env.example) and try again.")
        return 1

    from monitor import Monitor
    mon = Monitor(s)
    try:
        if args.once:
            mon.run_once()
        else:
            mon.run_forever()
    except KeyboardInterrupt:
        log.info("Stopped by user.")
    finally:
        mon.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
