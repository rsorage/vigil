import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import config
from storage.db import Database, _start_of_day, _truncate_to_hour
from storage.models import ErrorHourlyStat, ErrorRecord, ErrorStatus

logger = logging.getLogger(__name__)

SPARKLINE_HOURS = 48


def _get_env() -> Environment:
    templates_dir = Path(__file__).parent / "templates"
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )


def _analysis_dict(record: ErrorRecord) -> dict | None:
    if not record.analysis:
        return None
    if isinstance(record.analysis, dict):
        return record.analysis
    return record.analysis.model_dump()


def _build_sparkline_data(stats: list[ErrorHourlyStat], hours: int = SPARKLINE_HOURS) -> dict:
    now_bucket = _truncate_to_hour(datetime.now(timezone.utc))
    buckets: dict[datetime, int] = {}
    for i in range(hours):
        bucket = now_bucket - timedelta(hours=hours - 1 - i)
        buckets[bucket] = 0
    for stat in stats:
        hour = stat.hour.replace(tzinfo=timezone.utc) if stat.hour.tzinfo is None else stat.hour
        bucket = _truncate_to_hour(hour)
        if bucket in buckets:
            buckets[bucket] = stat.count

    counts = list(buckets.values())
    hours_list = [h.strftime("%Y-%m-%d %H:%M") for h in buckets.keys()]
    max_count = max(counts) if any(counts) else 1

    points = []
    for i, c in enumerate(counts):
        x = (i / (len(counts) - 1)) * 100 if len(counts) > 1 else 50
        y = 100 - (c / max_count * 85)
        points.append((round(x, 2), round(y, 2)))

    recent   = sum(counts[-6:])
    previous = sum(counts[-12:-6])
    if recent > previous * 1.2:
        trend = "rising"
    elif recent < previous * 0.8:
        trend = "falling"
    else:
        trend = "stable"

    return {
        "points": points,
        "trend": trend,
        "max_count": max_count,
        "hourly": [{"hour": h, "count": c} for h, c in zip(hours_list, counts)],
        "has_data": any(counts),
    }


def _build_diff(
    active: list[ErrorRecord],
    resolved_today: list[ErrorRecord],
    report_date: date,
) -> dict:
    """
    Compute what changed since the start of report_date (UTC).

    - new:      first_seen >= start of report_date
    - ongoing:  active and first_seen < start of report_date
    - resolved: went inactive on report_date
    """
    day_start = _start_of_day(
        datetime(report_date.year, report_date.month, report_date.day, tzinfo=timezone.utc)
    )

    new_errors     = [r for r in active if r.first_seen and r.first_seen >= day_start]
    ongoing_errors = [r for r in active if not r.first_seen or r.first_seen < day_start]

    return {
        "new_count":      len(new_errors),
        "ongoing_count":  len(ongoing_errors),
        "resolved_count": len(resolved_today),
        "has_changes":    bool(new_errors or resolved_today),
    }


def _serialise_error(r: ErrorRecord, sparkline: dict) -> dict:
    return {
        "fingerprint":      r.fingerprint,
        "logger_name":      r.logger_name,
        "file_path":        r.file_path,
        "line_number":      r.line_number,
        "message_template": r.message_template,
        "sample_traceback": r.sample_traceback,
        "occurrence_count": r.occurrence_count,
        "first_seen":       r.first_seen,
        "last_seen":        r.last_seen,
        "resolved_at":      r.resolved_at if hasattr(r, "resolved_at") else None,
        "status":           r.status.value,
        "analysis":         _analysis_dict(r),
        "sparkline":        sparkline,
    }


def _build_context(
    records: list[ErrorRecord],
    report_date: date,
    resolved_today: list[ErrorRecord],
    db: Database | None,
) -> dict:
    all_fps = [r.fingerprint for r in records] + [r.fingerprint for r in resolved_today]
    stats_by_fp: dict = {}
    if db is not None and all_fps:
        stats_by_fp = db.get_hourly_stats_bulk(all_fps)

    empty_sparkline = _build_sparkline_data([])

    errors = [
        _serialise_error(r, _build_sparkline_data(stats_by_fp.get(r.fingerprint, [])))
        for r in records
    ]
    resolved_serialised = [
        _serialise_error(r, empty_sparkline)
        for r in resolved_today
    ]

    diff = _build_diff(records, resolved_today, report_date)

    total_occurrences = sum(r.occurrence_count for r in records)
    analyzed = sum(1 for r in records if r.status == ErrorStatus.ANALYZED)
    pending  = sum(1 for r in records if r.status == ErrorStatus.NEW)

    return {
        "report_date":       report_date.isoformat(),
        "generated_at":      datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "errors":            errors,
        "resolved_today":    resolved_serialised,
        "diff":              diff,
        "total_unique":      len(records),
        "total_occurrences": total_occurrences,
        "total_analyzed":    analyzed,
        "total_pending":     pending,
    }


def render_digest(
    records: list[ErrorRecord],
    report_date: date | None = None,
    db: Database | None = None,
    resolved_today: list[ErrorRecord] | None = None,
) -> str:
    if report_date is None:
        report_date = date.today()
    if resolved_today is None:
        resolved_today = db.get_recently_resolved() if db else []

    env = _get_env()
    template = env.get_template("digest.html")
    context = _build_context(records, report_date, resolved_today, db)
    return template.render(**context)


def write_digest(
    records: list[ErrorRecord],
    report_date: date | None = None,
    db: Database | None = None,
) -> Path:
    if report_date is None:
        report_date = date.today()

    # Fetch resolved errors inside write_digest so callers don't have to
    resolved_today = db.get_recently_resolved() if db else []

    reports_dir = Path(config.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    report_path = reports_dir / f"{report_date.isoformat()}.html"
    html = render_digest(records, report_date, db=db, resolved_today=resolved_today)
    report_path.write_text(html, encoding="utf-8")
    logger.info("Report written to %s", report_path)

    _write_index(reports_dir)
    return report_path


def _write_index(reports_dir: Path) -> None:
    reports = sorted(reports_dir.glob("[0-9][0-9][0-9][0-9]-*.html"), reverse=True)
    report_names = [r.name for r in reports if r.name != "index.html"]
    env = _get_env()
    template = env.get_template("index.html")
    html = template.render(reports=report_names)
    (reports_dir / "index.html").write_text(html, encoding="utf-8")
