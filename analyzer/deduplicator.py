import hashlib
import logging
import re
from datetime import datetime, timezone

from storage.models import ErrorRecord, LogEvent

logger = logging.getLogger(__name__)

# Patterns to strip from messages before fingerprinting.
# Order matters: more specific patterns first.
_NORMALIZATIONS: list[tuple[re.Pattern, str]] = [
    # IDs: tenant_id=ten_abc123, device_id=dev_XYZ
    (re.compile(r'\b(tenant_id|device_id|user_id|session_id)=\S+'), r'\1=<id>'),
    # Prefixed IDs: ten_Abc123, dev_XYZ789, usr_..., etc.
    (re.compile(r'\b(ten|dev|usr|org|drv|veh)_[A-Za-z0-9]+'), r'\1_<id>'),
    # UUIDs
    (re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.I), '<uuid>'),
    # Numeric values (floats and ints) — e.g. "input_value=147.906"
    (re.compile(r'\binput_value=[\d.]+'), 'input_value=<val>'),
    (re.compile(r'\b\d+\.\d+\b'), '<float>'),
    # IP addresses
    (re.compile(r'\b\d{1,3}(?:\.\d{1,3}){3}(:\d+)?\b'), '<ip>'),
    # Port numbers standalone
    (re.compile(r':\d{4,5}\b'), ':<port>'),
    # Timestamps inside messages
    (re.compile(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?'), '<ts>'),
    # Firmware / semver versions
    (re.compile(r'\b\d+\.\d+\.\d+\b'), '<version>'),
    # Quoted string values (e.g. JSON field values)
    (re.compile(r':\s*"[^"]{6,}"'), ': "<str>"'),
    # Standalone long hex strings (e.g. tokens, hashes)
    (re.compile(r'\b[0-9a-f]{16,}\b', re.I), '<hex>'),
]


def _normalize_message(message: str) -> str:
    """Strip variable parts from a log message to produce a stable template."""
    normalized = message
    for pattern, replacement in _NORMALIZATIONS:
        normalized = pattern.sub(replacement, normalized)
    # Collapse multiple spaces
    normalized = re.sub(r' {2,}', ' ', normalized).strip()
    return normalized


def _fingerprint(logger_name: str, message_template: str, file_path: str | None, line_number: int | None) -> str:
    """
    SHA-256 fingerprint of the stable parts of an error.
    file_path + line_number are included when available (traceback errors);
    logger_name + message_template cover errors without tracebacks.
    """
    key = "|".join([
        logger_name,
        message_template,
        file_path or "",
        str(line_number or ""),
    ])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def deduplicate(events: list[LogEvent]) -> list[ErrorRecord]:
    """
    Collapse a list of error LogEvents into unique ErrorRecords.

    Events with the same fingerprint are merged: occurrence_count is summed,
    first_seen / last_seen track the time range within this batch.

    Returns one ErrorRecord per unique error fingerprint, ready to be
    handed off to Database.upsert_error().
    """
    seen: dict[str, ErrorRecord] = {}

    for event in events:
        template = _normalize_message(event.message)
        fp = _fingerprint(event.logger_name, template, event.file_path, event.line_number)

        if fp in seen:
            existing = seen[fp]
            existing.occurrence_count += 1
            if event.timestamp > existing.last_seen:
                existing.last_seen = event.timestamp
            if event.timestamp < existing.first_seen:
                existing.first_seen = event.timestamp
        else:
            seen[fp] = ErrorRecord(
                fingerprint=fp,
                logger_name=event.logger_name,
                message_template=template,
                sample_traceback=event.traceback,
                file_path=event.file_path,
                line_number=event.line_number,
                occurrence_count=1,
                first_seen=event.timestamp,
                last_seen=event.timestamp,
            )

    records = list(seen.values())
    logger.info(
        "Deduplicated %d error events → %d unique errors",
        len(events),
        len(records),
    )
    return records
