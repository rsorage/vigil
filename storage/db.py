from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from sqlmodel import Session, SQLModel, create_engine, select

from storage.models import ErrorAnalysis, ErrorRecord, ErrorStatus


class Database:
    def __init__(self, db_path: str | Path = "errors.db"):
        db_url = f"sqlite:///{db_path}"
        # expire_on_commit=False: objects remain usable after their session closes,
        # which matters when callers hold references across multiple operations.
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
    # Writes
    # -------------------------------------------------------------------------

    def upsert_error(self, record: ErrorRecord) -> None:
        """
        Insert a new error or increment occurrence_count + update last_seen.
        If an inactive error reappears it resets to NEW so it gets re-analyzed.

        Note: always pass a freshly constructed ErrorRecord; do not reuse an
        instance that was previously handed to this method, as SQLAlchemy will
        have attached it to a closed session.
        """
        # Extract scalar values before opening the session to avoid any
        # detached-instance issues if the caller inadvertently reuses objects.
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
    # Reads
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
