"""
Daily digest entry point.
Runs once daily via cron:
  0 18 * * * cd /home/ubuntu/vigil && .venv/bin/python digest.py
"""
import logging
import sys

from analyzer.code_reader import read_context_for_error
from llm import get_provider
from reporting.renderer import write_digest
from storage import Database
from storage.models import ErrorStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def analyze_single_error(db: Database, error, provider=None):
    """
    Run LLM analysis on a single ErrorRecord and persist the result.
    Returns the ErrorAnalysis on success, raises on failure.
    Extracted so both digest.py and the CLI can call it directly.
    """
    if provider is None:
        provider = get_provider()
    code_context = read_context_for_error(error.file_path, error.line_number)
    analysis = provider.analyze_error(error, code_context)
    db.save_analysis(error.fingerprint, analysis)
    return analysis


def analyze_new_errors(db: Database) -> int:
    """
    Run LLM analysis on all NEW errors, persist results, and return count.
    Already-analyzed errors are skipped — this is idempotent.
    """
    new_errors = db.get_by_status(ErrorStatus.NEW)

    if not new_errors:
        logger.info("No new errors to analyze")
        return 0

    logger.info("Analyzing %d new error(s)...", len(new_errors))
    provider = get_provider()
    analyzed = 0

    for error in new_errors:
        try:
            code_context = read_context_for_error(error.file_path, error.line_number)
            if code_context:
                logger.info("  [%s] sending with code context (%d chars)", error.fingerprint, len(code_context))
            else:
                logger.info("  [%s] sending without code context (no file/line resolved)", error.fingerprint)

            analysis = analyze_single_error(db, error, provider)
            analyzed += 1
            logger.info("  [%s] ✓ confidence=%s — %s", error.fingerprint, analysis.confidence, analysis.short_description)

        except Exception as e:
            logger.error("  [%s] analysis failed: %s", error.fingerprint, e)

    return analyzed


def main() -> None:
    logger.info("Daily digest starting")
    db = Database()
    db.initialize()

    analyzed = analyze_new_errors(db)

    active = db.get_all_active()
    new_remaining = [e for e in active if e.status == ErrorStatus.NEW]

    logger.info(
        "Digest summary: %d analyzed, %d total active, %d still pending",
        analyzed,
        len(active),
        len(new_remaining),
    )

    report_path = write_digest(active, db=db)
    logger.info("Report available at %s", report_path)


if __name__ == "__main__":
    main()
