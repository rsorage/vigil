"""
Schema migrations against an existing errors.db.

The pre-multi-service schema is recreated by hand here rather than checked in
as a fixture file, so these tests fail loudly if a migration stops being
additive — that is exactly the case a production database would hit.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from storage.db import Database
from storage.models import ErrorRecord, ErrorStatus

# The `errors` table as it existed before the `service` column was added.
_LEGACY_ERRORS_DDL = """
CREATE TABLE errors (
    fingerprint VARCHAR NOT NULL PRIMARY KEY,
    logger_name VARCHAR NOT NULL,
    message_template VARCHAR NOT NULL,
    sample_traceback VARCHAR,
    file_path VARCHAR,
    line_number INTEGER,
    occurrence_count INTEGER NOT NULL,
    first_seen DATETIME NOT NULL,
    last_seen DATETIME NOT NULL,
    status VARCHAR NOT NULL,
    resolved_at DATETIME,
    github_issue_url VARCHAR,
    analysis JSON
)
"""

_LEGACY_ROW = """
INSERT INTO errors (
    fingerprint, logger_name, message_template, occurrence_count,
    first_seen, last_seen, status
-- SQLAlchemy persists Enum columns by member NAME, not value.
) VALUES ('deadbeef', 'app.legacy', 'boom', 7, '2026-07-20 10:00:00',
          '2026-07-20 11:00:00', 'ANALYZED')
"""


@pytest.fixture
def legacy_db(tmp_path) -> Database:
    """A database on the old schema, with one row already in it."""
    db = Database(tmp_path / "errors.db")
    with db.engine.begin() as conn:
        conn.execute(text(_LEGACY_ERRORS_DDL))
        conn.execute(text(_LEGACY_ROW))
    return db


def _columns(db: Database, table: str) -> set[str]:
    with db.engine.begin() as conn:
        return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


class TestServiceColumnMigration:
    def test_column_added_to_existing_table(self, legacy_db):
        assert "service" not in _columns(legacy_db, "errors")
        legacy_db.initialize()
        assert "service" in _columns(legacy_db, "errors")

    def test_existing_rows_readable_after_migration(self, legacy_db):
        legacy_db.initialize()
        record = legacy_db.get_by_fingerprint("deadbeef")
        assert record is not None
        assert record.occurrence_count == 7
        assert record.status == ErrorStatus.ANALYZED

    def test_existing_rows_backfilled_with_empty_service(self, legacy_db):
        legacy_db.initialize()
        record = legacy_db.get_by_fingerprint("deadbeef")
        assert record.service == ""

    def test_writes_after_migration_keep_service(self, legacy_db):
        legacy_db.initialize()
        legacy_db.upsert_error(
            ErrorRecord(
                fingerprint="cafe1234",
                service="worker",
                logger_name="app.jobs",
                message_template="scheduler failed",
                first_seen=datetime(2026, 7, 30, tzinfo=timezone.utc),
                last_seen=datetime(2026, 7, 30, tzinfo=timezone.utc),
            )
        )
        assert legacy_db.get_by_fingerprint("cafe1234").service == "worker"

    def test_is_idempotent(self, legacy_db):
        legacy_db.initialize()
        legacy_db.initialize()  # must not raise "duplicate column name"
        assert "service" in _columns(legacy_db, "errors")


class TestFreshDatabase:
    def test_new_database_has_service_column(self, tmp_path):
        db = Database(tmp_path / "fresh.db")
        db.initialize()
        assert "service" in _columns(db, "errors")
