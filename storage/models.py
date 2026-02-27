from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel
from sqlmodel import Column, Field, SQLModel, UniqueConstraint
from sqlalchemy import JSON


class ErrorStatus(str, Enum):
    NEW = "new"
    ANALYZED = "analyzed"
    INACTIVE = "inactive"


# ---------------------------------------------------------------------------
# Non-persisted dataclass: represents a single parsed log entry
# ---------------------------------------------------------------------------

@dataclass
class LogEvent:
    """A single parsed log entry, potentially spanning multiple raw lines."""
    timestamp: datetime
    logger_name: str
    level: str
    message: str
    raw_lines: list[str] = field(default_factory=list)
    traceback: Optional[str] = None
    file_path: Optional[str] = None
    line_number: Optional[int] = None


# ---------------------------------------------------------------------------
# Pydantic model: structured LLM output, stored as JSON column
# ---------------------------------------------------------------------------

class ErrorAnalysis(BaseModel):
    """Structured output from LLM analysis. Stored as a JSON column."""
    short_description: str
    root_cause: str
    suggested_fix: str
    confidence: str  # "high" | "medium" | "low"


# ---------------------------------------------------------------------------
# SQLModel table: the core persisted entity
# ---------------------------------------------------------------------------

class ErrorRecord(SQLModel, table=True):
    __tablename__ = "errors"

    fingerprint: str = Field(primary_key=True)
    logger_name: str
    message_template: str
    sample_traceback: Optional[str] = None
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    occurrence_count: int = Field(default=1)
    first_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: ErrorStatus = Field(default=ErrorStatus.NEW)

    analysis: Optional[ErrorAnalysis] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )


# ---------------------------------------------------------------------------
# SQLModel table: per-hour occurrence counts for trend sparklines
# ---------------------------------------------------------------------------

class ErrorHourlyStat(SQLModel, table=True):
    __tablename__ = "error_hourly_stats"
    __table_args__ = (UniqueConstraint("fingerprint", "hour"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    fingerprint: str = Field(index=True)
    # Truncated to the start of the hour, always UTC
    hour: datetime
    count: int = Field(default=0)
