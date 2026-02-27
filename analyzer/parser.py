import logging
import re
from datetime import datetime, timezone
from typing import Optional

from storage.models import LogEvent

logger = logging.getLogger(__name__)

# Matches the start of a new log entry:
# 2026-02-24 13:11:14 - app.services.device_auth - INFO - some message
_LOG_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
    r"\s+-\s+(?P<logger>[\w.]+)"
    r"\s+-\s+(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)"
    r"\s+-\s+(?P<message>.*)$"
)

# Matches a file reference inside a traceback:
# File "/app/app/device_management/infrastructure/mqtt/device_vitals_processor.py", line 100, in process
_TRACEBACK_FILE_RE = re.compile(
    r'File "(?P<path>[^"]+)", line (?P<line>\d+)'
)

# Strips the docker compose log prefix if --no-log-prefix was not used:
# "api-1  | " or "api-prod-1  | "
_PREFIX_RE = re.compile(r"^\S+-\d+\s+\|\s+")

# Path fragments that indicate a venv or stdlib frame — not app code
_VENV_PATH_FRAGMENTS = (".venv", "site-packages", "dist-packages")


def _strip_prefix(line: str) -> str:
    return _PREFIX_RE.sub("", line)


def _parse_timestamp(raw: str) -> datetime:
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _is_app_frame(path: str) -> bool:
    """Return True if the path belongs to app code rather than venv/stdlib."""
    return not any(fragment in path for fragment in _VENV_PATH_FRAGMENTS)


def _extract_traceback_location(traceback: str) -> tuple[Optional[str], Optional[int]]:
    """
    Return the (file_path, line_number) of the innermost *app* frame in a
    traceback — the last File "..." line that is not inside .venv or
    site-packages. Falls back to the absolute innermost frame if no app
    frame exists (e.g. error originates entirely inside a library).
    """
    matches = _TRACEBACK_FILE_RE.findall(traceback)
    if not matches:
        return None, None

    # Walk from innermost (last) outward, return first app frame found
    for path, line in reversed(matches):
        if _is_app_frame(path):
            return path, int(line)

    # Fallback: no app frame found, use absolute innermost
    path, line = matches[-1]
    return path, int(line)


def parse_logs(raw: str) -> list[LogEvent]:
    """
    Parse raw docker compose log output into a list of LogEvent objects.

    Multiline entries (tracebacks, validation error details) are grouped
    with their originating log line using the timestamp as a boundary marker.
    """
    events: list[LogEvent] = []
    current_lines: list[str] = []
    current_match: Optional[re.Match] = None

    def flush() -> None:
        """Finalise the current accumulated entry and append to events."""
        if current_match is None:
            return

        message = current_match.group("message")
        level = current_match.group("level")
        continuation = current_lines[1:]  # lines after the first

        traceback: Optional[str] = None
        file_path: Optional[str] = None
        line_number: Optional[int] = None

        if continuation:
            full_extra = "\n".join(continuation)
            # Attach continuation to message for context, extract traceback block
            if "Traceback (most recent call last)" in full_extra:
                tb_start = full_extra.index("Traceback (most recent call last)")
                traceback = full_extra[tb_start:]
                # Prepend any pre-traceback detail to the message
                pre = full_extra[:tb_start].strip()
                if pre:
                    message = f"{message}\n{pre}"
                file_path, line_number = _extract_traceback_location(traceback)
            else:
                # Validation error detail lines or other continuation without traceback
                message = f"{message}\n{full_extra}"

        events.append(
            LogEvent(
                timestamp=_parse_timestamp(current_match.group("timestamp")),
                logger_name=current_match.group("logger"),
                level=level,
                message=message,
                raw_lines=list(current_lines),
                traceback=traceback,
                file_path=file_path,
                line_number=line_number,
            )
        )

    for raw_line in raw.splitlines():
        line = _strip_prefix(raw_line)
        if not line.strip():
            continue

        m = _LOG_LINE_RE.match(line)
        if m:
            flush()
            current_match = m
            current_lines = [line]
        else:
            # Continuation line — belongs to the current entry
            if current_match is not None:
                current_lines.append(line)
            # Lines before the first recognizable entry are silently dropped

    flush()  # don't forget the last entry

    logger.info(
        "Parsed %d log events (%d errors)",
        len(events),
        sum(1 for e in events if e.level == "ERROR"),
    )
    return events


def filter_errors(events: list[LogEvent]) -> list[LogEvent]:
    """Return only ERROR and CRITICAL level events."""
    return [e for e in events if e.level in ("ERROR", "CRITICAL")]
