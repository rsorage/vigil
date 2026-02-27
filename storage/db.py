from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, Optional

from sqlmodel import Session, SQLModel, create_engine, select

from storage.models import ErrorAnalysis, ErrorHourlyStat, ErrorRecord, ErrorStatus


def _truncate_to_hour(dt: datetime) -> datetime:
    """Return dt with minutes/seconds/microseconds zeroed, UTC."""
    return dt.replace(minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


class Database:
    def __init__(self, db_path: str | Path = "errors.db"):
        db_url = f"sqlite:///{db_path}"
        self.engine = create_engine(
            db_url,
            echo=False,
            connect_args={"check_same_thread": False},
        )
        self._session_factory = lambda: Session(
            self.engine, expire_on_commit=False
        )

    @contextmanager
    def _session(self) -> Generator[Session, None, None]:
        with self._session_factory() as session:
            yield session

    def initialize(self) -> None:
        """Create all tables if they don't exist."""
        SQLModel.metadata.create_all(self.engine)

    # -------------------------------------------------------------------------
    # Writes — errors
    # -------------------------------------------------------------------------

    def upsert_error(self, record: ErrorRecord) -> None:
        """
        Insert a new error or increment occurrence_count + update last_seen.
        If an inactive error reappears it resets to NEW so it gets re-analyzed.
        """
        fingerprint = str(record.fingerprint)
        with self._session() as session:
            existing = session.get(ErrorRecord, fingerprint)

            if existing is None:
                session.add(record)
            else:
                existing.occurrence_count += record.occurrence_count
                existing.last_seen = record.last_seen
                if existing.status == ErrorStatus.INACTIVE:
                    existing.status = ErrorStatus.NEW

            session.commit()

    def save_analysis(self, fingerprint: str, analysis: ErrorAnalysis) -> None:
        """Persist LLM analysis and transition status to ANALYZED."""
        with self._session() as session:
            record = session.get(ErrorRecord, fingerprint)
            if record is None:
                raise ValueError(f"No error found with fingerprint {fingerprint!r}")
            record.analysis = analysis.model_dump()
            record.status = ErrorStatus.ANALYZED
            session.commit()

    def mark_inactive(self, fingerprints: list[str]) -> int:
        """Mark a batch of errors as inactive. Returns count updated."""
        if not fingerprints:
            return 0
        with self._session() as session:
            records = session.exec(
                select(ErrorRecord).where(ErrorRecord.fingerprint.in_(fingerprints))
            ).all()
            for record in records:
                record.status = ErrorStatus.INACTIVE
            session.commit()
            return len(records)

    # -------------------------------------------------------------------------
    # Writes — hourly stats
    # -------------------------------------------------------------------------

    def upsert_hourly_stat(self, fingerprint: str, count: int, hour: datetime | None = None) -> None:
        """
        Record occurrence count for a given error in the current hour.
        If called multiple times in the same hour (e.g. during testing),
        the count is accumulated.
        """
        bucket = _truncate_to_hour(hour or datetime.now(timezone.utc))
        with self._session() as session:
            existing = session.exec(
                select(ErrorHourlyStat)
                .where(ErrorHourlyStat.fingerprint == fingerprint)
                .where(ErrorHourlyStat.hour == bucket)
            ).first()

            if existing is None:
                session.add(ErrorHourlyStat(
                    fingerprint=fingerprint,
                    hour=bucket,
                    count=count,
                ))
            else:
                existing.count += count

            session.commit()

    # -------------------------------------------------------------------------
    # Reads — errors
    # -------------------------------------------------------------------------

    def get_by_fingerprint(self, fingerprint: str) -> Optional[ErrorRecord]:
        with self._session() as session:
            return session.get(ErrorRecord, fingerprint)

    def get_by_status(self, status: ErrorStatus) -> list[ErrorRecord]:
        with self._session() as session:
            return session.exec(
                select(ErrorRecord)
                .where(ErrorRecord.status == status)
                .order_by(ErrorRecord.occurrence_count.desc())
            ).all()

    def get_stale_fingerprints(self, older_than: datetime) -> list[str]:
        """Return fingerprints of non-inactive errors not seen since `older_than`."""
        with self._session() as session:
            records = session.exec(
                select(ErrorRecord)
                .where(ErrorRecord.status != ErrorStatus.INACTIVE)
                .where(ErrorRecord.last_seen < older_than)
            ).all()
            return [r.fingerprint for r in records]

    def get_all_active(self) -> list[ErrorRecord]:
        """All non-inactive errors, for the daily digest."""
        with self._session() as session:
            return session.exec(
                select(ErrorRecord)
                .where(ErrorRecord.status != ErrorStatus.INACTIVE)
                .order_by(ErrorRecord.occurrence_count.desc())
            ).all()

    # -------------------------------------------------------------------------
    # Reads — hourly stats
    # -------------------------------------------------------------------------

    def get_hourly_stats(
        self,
        fingerprint: str,
        hours: int = 48,
    ) -> list[ErrorHourlyStat]:
        """Return up to `hours` most recent hourly stat rows for an error."""
        since = _truncate_to_hour(datetime.now(timezone.utc)) - timedelta(hours=hours - 1)
        with self._session() as session:
            return session.exec(
                select(ErrorHourlyStat)
                .where(ErrorHourlyStat.fingerprint == fingerprint)
                .where(ErrorHourlyStat.hour >= since)
                .order_by(ErrorHourlyStat.hour.asc())
            ).all()

    def get_hourly_stats_bulk(
        self,
        fingerprints: list[str],
        hours: int = 48,
    ) -> dict[str, list[ErrorHourlyStat]]:
        """
        Fetch hourly stats for multiple errors in a single query.
        Returns a dict keyed by fingerprint.
        """
        if not fingerprints:
            return {}
        since = _truncate_to_hour(datetime.now(timezone.utc)) - timedelta(hours=hours - 1)
        with self._session() as session:
            rows = session.exec(
                select(ErrorHourlyStat)
                .where(ErrorHourlyStat.fingerprint.in_(fingerprints))
                .where(ErrorHourlyStat.hour >= since)
                .order_by(ErrorHourlyStat.hour.asc())
            ).all()

        result: dict[str, list[ErrorHourlyStat]] = {fp: [] for fp in fingerprints}
        for row in rows:
            result[row.fingerprint].append(row)
        return result
