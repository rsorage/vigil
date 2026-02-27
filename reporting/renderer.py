import logging
from datetime import date, datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import config
from storage.models import ErrorAnalysis, ErrorRecord, ErrorStatus

logger = logging.getLogger(__name__)


def _get_env() -> Environment:
    templates_dir = Path(__file__).parent / "templates"
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )


def _analysis_dict(record: ErrorRecord) -> dict | None:
    """Safely coerce the JSON analysis blob back to a typed dict."""
    if not record.analysis:
        return None
    if isinstance(record.analysis, dict):
        return record.analysis
    # ErrorAnalysis pydantic model
    return record.analysis.model_dump()


def _build_context(records: list[ErrorRecord], report_date: date) -> dict:
    errors = []
    for r in records:
        analysis = _analysis_dict(r)
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


def render_digest(records: list[ErrorRecord], report_date: date | None = None) -> str:
    """Render the daily digest HTML and return it as a string."""
    if report_date is None:
        report_date = date.today()

    env = _get_env()
    template = env.get_template("digest.html")
    context = _build_context(records, report_date)
    return template.render(**context)


def write_digest(records: list[ErrorRecord], report_date: date | None = None) -> Path:
    """
    Render the digest and write it to the reports directory.
    Also regenerates index.html. Returns the path of the written report.
    """
    if report_date is None:
        report_date = date.today()

    reports_dir = Path(config.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Write daily report
    report_path = reports_dir / f"{report_date.isoformat()}.html"
    html = render_digest(records, report_date)
    report_path.write_text(html, encoding="utf-8")
    logger.info("Report written to %s", report_path)

    # Regenerate index
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
