import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import config
from storage.db import Database, _truncate_to_hour
from storage.models import ErrorAnalysis, ErrorHourlyStat, ErrorRecord, ErrorStatus

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


def _build_sparkline_data(
    stats: list[ErrorHourlyStat],
    hours: int = SPARKLINE_HOURS,
) -> dict:
    """
    Build a normalised sparkline dataset from hourly stats.

    Returns a dict with:
      - points: list of (x_pct, y_pct) tuples for SVG polyline
      - trend:  "rising" | "falling" | "stable"
      - max_count: for tooltip labels
      - hourly: list of {hour, count} for tooltip data
    """
    now_bucket = _truncate_to_hour(datetime.now(timezone.utc))
    # Build a full 48-slot timeline, filling missing hours with 0
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

    # Normalised SVG points: x is 0–100%, y is inverted (SVG y=0 is top)
    points = []
    for i, c in enumerate(counts):
        x = (i / (len(counts) - 1)) * 100 if len(counts) > 1 else 50
        y = 100 - (c / max_count * 85)  # leave 15% padding at top
        points.append((round(x, 2), round(y, 2)))

    # Trend: compare last 6h vs previous 6h
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


def _build_context(
    records: list[ErrorRecord],
    report_date: date,
    db: Database | None = None,
) -> dict:
    fingerprints = [r.fingerprint for r in records]
    stats_by_fp: dict[str, list] = {}

    if db is not None and fingerprints:
        stats_by_fp = db.get_hourly_stats_bulk(fingerprints)

    errors = []
    for r in records:
        analysis = _analysis_dict(r)
        stats = stats_by_fp.get(r.fingerprint, [])
        sparkline = _build_sparkline_data(stats)
        errors.append({
            "fingerprint": r.fingerprint,
            "logger_name": r.logger_name,
            "file_path": r.file_path,
            "line_number": r.line_number,
            "message_template": r.message_template,
            "sample_traceback": r.sample_traceback,
            "occurrence_count": r.occurrence_count,
            "first_seen": r.first_seen,
            "last_seen": r.last_seen,
            "status": r.status.value,
            "analysis": analysis,
            "sparkline": sparkline,
        })

    total_occurrences = sum(r.occurrence_count for r in records)
    analyzed = sum(1 for r in records if r.status == ErrorStatus.ANALYZED)
    pending  = sum(1 for r in records if r.status == ErrorStatus.NEW)

    return {
        "report_date": report_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "errors": errors,
        "total_unique": len(records),
        "total_occurrences": total_occurrences,
        "total_analyzed": analyzed,
        "total_pending": pending,
    }


def render_digest(
    records: list[ErrorRecord],
    report_date: date | None = None,
    db: Database | None = None,
) -> str:
    """Render the daily digest HTML and return it as a string."""
    if report_date is None:
        report_date = date.today()

    env = _get_env()
    template = env.get_template("digest.html")
    context = _build_context(records, report_date, db=db)
    return template.render(**context)


def write_digest(
    records: list[ErrorRecord],
    report_date: date | None = None,
    db: Database | None = None,
) -> Path:
    """
    Render the digest and write it to the reports directory.
    Also regenerates index.html. Returns the path of the written report.
    """
    if report_date is None:
        report_date = date.today()

    reports_dir = Path(config.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    report_path = reports_dir / f"{report_date.isoformat()}.html"
    html = render_digest(records, report_date, db=db)
    report_path.write_text(html, encoding="utf-8")
    logger.info("Report written to %s", report_path)

    _write_index(reports_dir)

    return report_path


def _write_index(reports_dir: Path) -> None:
    """Generate a simple index.html listing all daily reports."""
    reports = sorted(reports_dir.glob("[0-9][0-9][0-9][0-9]-*.html"), reverse=True)
    report_names = [r.name for r in reports if r.name != "index.html"]

    env = _get_env()
    template = env.get_template("index.html")
    html = template.render(reports=report_names)
    (reports_dir / "index.html").write_text(html, encoding="utf-8")
