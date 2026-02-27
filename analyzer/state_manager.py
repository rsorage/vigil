import logging
from datetime import datetime, timedelta, timezone

from config import config
from storage.db import Database
from storage.models import ErrorRecord

logger = logging.getLogger(__name__)


def persist_errors(db: Database, records: list[ErrorRecord]) -> None:
    """Upsert all deduplicated error records into the database."""
    for record in records:
        db.upsert_error(record)
    logger.info("Persisted %d error records", len(records))


def mark_stale_inactive(db: Database) -> int:
    """
    Mark any active error not seen in the last ERROR_INACTIVE_AFTER_HOURS
    as inactive. Returns the number of errors deactivated.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.error_inactive_after_hours)
    stale = db.get_stale_fingerprints(older_than=cutoff)
    count = db.mark_inactive(stale)
    if count:
        logger.info("Marked %d error(s) inactive (not seen since %s)", count, cutoff.isoformat())
    return count
