from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, Optional

from sqlmodel import Session, SQLModel, create_engine, select

from storage.models import ErrorAnalysis, ErrorHourlyStat, ErrorRecord, ErrorStatus


def _truncate_to_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


def _start_of_day(dt: datetime) -> datetime:
    # Return naive UTC — SQLite strips tzinfo, so we keep comparisons naive
    return dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


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
        SQLModel.metadata.create_all(self.engine)

    # -------------------------------------------------------------------------
    # Writes — errors
    # -------------------------------------------------------------------------

    def upsert_error(self, record: ErrorRecord) -> None:
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
                    existing.resolved_at = None  # reactivated — clear resolved_at
            session.commit()

    def save_analysis(self, fingerprint: str, analysis: ErrorAnalysis) -> None:
        with self._session() as session:
            record = session.get(ErrorRecord, fingerprint)
            if record is None:
                raise ValueError(f"No error found with fingerprint {fingerprint!r}")
            record.analysis = analysis.model_dump()
            record.status = ErrorStatus.ANALYZED
            session.commit()

    def mark_inactive(self, fingerprints: list[str]) -> int:
        """Mark a batch of errors as inactive and record when they were resolved."""
        if not fingerprints:
            return 0
        now = datetime.now(timezone.utc)
        with self._session() as session:
            records = session.exec(
                select(ErrorRecord).where(ErrorRecord.fingerprint.in_(fingerprints))
            ).all()
            for record in records:
                record.status = ErrorStatus.INACTIVE
                record.resolved_at = now
            session.commit()
            return len(records)

    # -------------------------------------------------------------------------
    # Writes — hourly stats
    # -------------------------------------------------------------------------

    def upsert_hourly_stat(self, fingerprint: str, count: int, hour: datetime | None = None) -> None:
        bucket = _truncate_to_hour(hour or datetime.now(timezone.utc))
        with self._session() as session:
            existing = session.exec(
                select(ErrorHourlyStat)
                .where(ErrorHourlyStat.fingerprint == fingerprint)
                .where(ErrorHourlyStat.hour == bucket)
            ).first()
            if existing is None:
                session.add(ErrorHourlyStat(fingerprint=fingerprint, hour=bucket, count=count))
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
        with self._session() as session:
            records = session.exec(
                select(ErrorRecord)
                .where(ErrorRecord.status != ErrorStatus.INACTIVE)
                .where(ErrorRecord.last_seen < older_than)
            ).all()
            return [r.fingerprint for r in records]

    def get_all_active(self) -> list[ErrorRecord]:
        with self._session() as session:
            return session.exec(
                select(ErrorRecord)
                .where(ErrorRecord.status != ErrorStatus.INACTIVE)
                .order_by(ErrorRecord.occurrence_count.desc())
            ).all()

    def save_github_issue_url(self, fingerprint: str, url: str) -> None:
        """Persist the GitHub issue URL on an error record."""
        with self._session() as session:
            record = session.get(ErrorRecord, fingerprint)
            if record is None:
                raise ValueError(f"No error found with fingerprint {fingerprint!r}")
            record.github_issue_url = url
            session.commit()

    def get_errors_with_issues(self) -> list[ErrorRecord]:
        """Return all errors that have an associated GitHub issue URL."""
        with self._session() as session:
            return session.exec(
                select(ErrorRecord)
                .where(ErrorRecord.github_issue_url.is_not(None))
                .order_by(ErrorRecord.last_seen.desc())
            ).all()

    def get_recently_resolved(self, since: datetime | None = None) -> list[ErrorRecord]:
        """
        Return errors that went inactive on or after `since`.
        Defaults to start of today (UTC) — i.e. "resolved today".
        """
        cutoff = since or _start_of_day(datetime.now(timezone.utc))
        with self._session() as session:
            return session.exec(
                select(ErrorRecord)
                .where(ErrorRecord.status == ErrorStatus.INACTIVE)
                .where(ErrorRecord.resolved_at >= cutoff)
                .order_by(ErrorRecord.resolved_at.desc())
            ).all()

    # -------------------------------------------------------------------------
    # Reads — hourly stats
    # -------------------------------------------------------------------------

    def get_hourly_stats(self, fingerprint: str, hours: int = 48) -> list[ErrorHourlyStat]:
        since = _truncate_to_hour(datetime.now(timezone.utc)) - timedelta(hours=hours - 1)
        with self._session() as session:
            return session.exec(
                select(ErrorHourlyStat)
                .where(ErrorHourlyStat.fingerprint == fingerprint)
                .where(ErrorHourlyStat.hour >= since)
                .order_by(ErrorHourlyStat.hour.asc())
            ).all()

    def get_hourly_stats_bulk(self, fingerprints: list[str], hours: int = 48) -> dict[str, list[ErrorHourlyStat]]:
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
