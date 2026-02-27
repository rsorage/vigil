"""
Hourly entry point.
Runs every hour via cron:
  0 * * * * cd /home/ubuntu/log-analyzer && .venv/bin/python hourly.py
"""
import logging
import sys

from analyzer.collector import CollectorError, collect_logs
from analyzer.deduplicator import deduplicate
from analyzer.parser import filter_errors, parse_logs
from analyzer.state_manager import mark_stale_inactive, persist_errors
from config import config
from storage import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Hourly run starting")
    db = Database()
    db.initialize()

    # 1. Collect
    try:
        raw = collect_logs(
            compose_file=config.docker_compose_file,
            service_name=config.docker_service_name,
            since="1h",
        )
    except CollectorError as e:
        logger.error("Log collection failed: %s", e)
        sys.exit(1)

    # 2. Parse → filter errors only
    events = parse_logs(raw)
    errors = filter_errors(events)

    if not errors:
        logger.info("No errors found in this hour's logs")
    else:
        # 3. Deduplicate
        records = deduplicate(errors)

        # 4. Persist
        persist_errors(db, records)

    # 5. Deactivate errors not seen in the last 48h
    mark_stale_inactive(db)

    logger.info("Hourly run complete")


if __name__ == "__main__":
    main()
