"""
Daily digest entry point.
Runs once daily via cron:
  0 18 * * * cd /home/ubuntu/log-analyzer && .venv/bin/python digest.py

Phase 1: initializes the database only.
Remaining logic added in subsequent phases.
"""
import logging

from config import config
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
    logger.info("Database ready at %s", db.db_path)
    # Phase 5 will add: analyze new errors → save analysis
    # Phase 6 will add: render HTML report → write to reports/


if __name__ == "__main__":
    main()
