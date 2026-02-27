"""
Daily digest entry point.
Runs once daily via cron:
  0 18 * * * cd /home/ubuntu/vigil && .venv/bin/python digest.py

Phase 5 will add: analyze new errors → save analysis
Phase 6 will add: render HTML report → write to reports/
"""
import logging

from storage import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Daily digest starting")
    db = Database()
    db.initialize()

    from storage.models import ErrorStatus
    new_errors = db.get_by_status(ErrorStatus.NEW)
    active_errors = db.get_all_active()

    logger.info(
        "Status: %d new error(s), %d total active",
        len(new_errors),
        len(active_errors),
    )
    logger.info("Daily digest complete (LLM analysis and reporting coming in phases 5–6)")


if __name__ == "__main__":
    main()
