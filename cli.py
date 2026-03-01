"""
Vigil CLI — inspect errors from the terminal.

Commands:
    uv run python cli.py errors              # list all active errors
    uv run python cli.py error <prefix>      # detail view for one error
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config import config
from storage.db import Database, _truncate_to_hour
from storage.models import ErrorRecord, ErrorStatus

console = Console()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_db() -> Database:
    db = Database(Path(config.reports_dir).parent / "errors.db")
    db.initialize()
    return db


def _status_style(status: str) -> str:
    return {"new": "bold cyan", "analyzed": "bold green", "inactive": "dim"}.get(status, "white")


def _confidence_style(confidence: str) -> str:
    return {"high": "bold green", "medium": "yellow", "low": "red"}.get(confidence, "white")


def _align_to_buckets(stats: list, hours: int = 24) -> list[int]:
    """
    Map stat rows onto a fixed-size hourly grid ending at the current hour.
    Missing buckets are filled with zero so the sparkline position reflects
    real time rather than just the order rows were inserted.
    """
    now_bucket = _truncate_to_hour(datetime.now(timezone.utc))
    buckets: dict[datetime, int] = {}
    for i in range(hours):
        b = now_bucket - timedelta(hours=hours - 1 - i)
        buckets[b] = 0
    for s in stats:
        hour = s.hour.replace(tzinfo=timezone.utc) if s.hour.tzinfo is None else s.hour
        b = _truncate_to_hour(hour)
        if b in buckets:
            buckets[b] = s.count
    return list(buckets.values())


def _sparkline(counts: list[int], width: int = 30) -> Text:
    """Render a unicode block sparkline from a list of counts."""
    blocks = " ▁▂▃▄▅▆▇█"
    if not counts or max(counts) == 0:
        bar = blocks[0] * width
        return Text(bar, style="dim")

    peak = max(counts)
    # Sample down to `width` buckets if we have more data points
    if len(counts) > width:
        step = len(counts) / width
        counts = [counts[int(i * step)] for i in range(width)]

    chars = "".join(blocks[min(int(c / peak * 8), 8)] for c in counts)
    # Colour by trend: compare last quarter vs previous quarter
    half = len(counts) // 2
    recent = sum(counts[half:])
    prev   = sum(counts[:half])
    if recent > prev * 1.2:
        style = "red"
    elif recent < prev * 0.8:
        style = "green"
    else:
        style = "blue"

    return Text(chars, style=style)


def _hourly_barchart(
    stats: list,   # list[ErrorHourlyStat]
    hours: int = 48,
    bar_width: int = 2,
) -> str:
    """
    Render a vertical bar chart using block characters, one column per hour.
    Returns a multi-line string ready to print.
    """
    blocks = [" ", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    chart_height = 8

    now_bucket = _truncate_to_hour(datetime.now(timezone.utc))
    buckets: dict[datetime, int] = {}
    for i in range(hours):
        b = now_bucket - timedelta(hours=hours - 1 - i)
        buckets[b] = 0
    for s in stats:
        hour = s.hour.replace(tzinfo=timezone.utc) if s.hour.tzinfo is None else s.hour
        b = _truncate_to_hour(hour)
        if b in buckets:
            buckets[b] = s.count

    counts = list(buckets.values())
    hours_list = list(buckets.keys())
    peak = max(counts) if any(counts) else 1

    # Build chart rows top-to-bottom
    rows = []
    for row in range(chart_height, 0, -1):
        threshold = peak * row / chart_height
        line = ""
        for c in counts:
            filled = c >= threshold
            line += ("█" if filled else " ") * bar_width
        rows.append(line)

    # X-axis label: mark every 6 hours with the hour number
    axis = ""
    for i, h in enumerate(hours_list):
        label = f"{h.hour:02d}" if h.hour % 6 == 0 else " " * 2
        axis += label[:bar_width].ljust(bar_width)

    rows.append("─" * (len(counts) * bar_width))
    rows.append(axis.rstrip())
    rows.append(f"peak: {peak}  ·  last {hours}h")

    return "\n".join(rows)


def _resolve_fingerprint(db: Database, prefix: str) -> ErrorRecord | None:
    """Find an error by fingerprint prefix (case-insensitive). Errors if ambiguous."""
    from sqlmodel import Session, select
    from storage.models import ErrorRecord

    prefix = prefix.lower()
    with Session(db.engine) as session:
        all_errors = session.exec(select(ErrorRecord)).all()

    matches = [e for e in all_errors if e.fingerprint.lower().startswith(prefix)]

    if len(matches) == 0:
        console.print(f"[red]No error found with fingerprint starting with '{prefix}'[/red]")
        return None
    if len(matches) > 1:
        console.print(f"[yellow]Ambiguous prefix '{prefix}' matches {len(matches)} errors:[/yellow]")
        for m in matches:
            console.print(f"  [dim]{m.fingerprint}[/dim]  {m.logger_name}")
        return None

    return matches[0]


# ── Commands ──────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """Vigil — inspect your error database from the terminal."""
    pass


@cli.command("errors")
@click.option("--all", "show_all", is_flag=True, help="Include inactive errors.")
def list_errors(show_all: bool):
    """List active errors, sorted by occurrence count."""
    db = _get_db()

    if show_all:
        from sqlmodel import Session, select
        with Session(db.engine) as session:
            errors = session.exec(
                select(ErrorRecord).order_by(ErrorRecord.occurrence_count.desc())
            ).all()
    else:
        errors = sorted(db.get_all_active(), key=lambda e: e.occurrence_count, reverse=True)

    if not errors:
        console.print("[dim]No errors found.[/dim]")
        return

    # Fetch sparkline data for all errors in one query
    fingerprints = [e.fingerprint for e in errors]
    stats_by_fp = db.get_hourly_stats_bulk(fingerprints, hours=24)

    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold dim",
        pad_edge=False,
        expand=True,
    )
    table.add_column("FINGERPRINT", style="dim", width=10, no_wrap=True)
    table.add_column("LOGGER", no_wrap=True, ratio=3)
    table.add_column("COUNT", justify="right", width=7)
    table.add_column("STATUS", width=10)
    table.add_column("CONFIDENCE", width=10)
    table.add_column("LAST SEEN", width=17)
    table.add_column("24H TREND", width=30, no_wrap=True)

    for e in errors:
        stats = stats_by_fp.get(e.fingerprint, [])
        counts = _align_to_buckets(stats, hours=24)
        spark = _sparkline(counts, width=28)

        confidence = e.analysis.get("confidence") if isinstance(e.analysis, dict) else (
            e.analysis.confidence if e.analysis else None
        )
        last_seen = e.last_seen.strftime("%m-%d %H:%M") if e.last_seen else "—"

        table.add_row(
            e.fingerprint[:8],
            e.logger_name,
            str(e.occurrence_count),
            Text(e.status.value, style=_status_style(e.status.value)),
            Text(confidence or "—", style=_confidence_style(confidence or "")),
            last_seen,
            spark,
        )

    console.print()
    console.print(table)
    console.print(
        f"  [dim]{len(errors)} error{'s' if len(errors) != 1 else ''}  ·  "
        f"[/dim][bold]vigil error <prefix>[/bold][dim] for details[/dim]"
    )
    console.print()


@cli.command("error")
@click.argument("prefix")
@click.option("--hours", default=48, show_default=True, help="Hours of history to show in chart.")
def error_detail(prefix: str, hours: int):
    """Show full detail for a single error by fingerprint prefix."""
    db = _get_db()
    record = _resolve_fingerprint(db, prefix)
    if record is None:
        return

    analysis = record.analysis
    if isinstance(analysis, dict):
        from storage.models import ErrorAnalysis
        analysis = ErrorAnalysis(**analysis) if analysis else None

    confidence = analysis.confidence if analysis else None
    status_style = _status_style(record.status.value)
    conf_style   = _confidence_style(confidence or "")

    # ── Header ────────────────────────────────────────────────────────────────
    header = Text()
    header.append(record.logger_name, style="bold yellow")
    if record.file_path:
        header.append(f"\n{record.file_path}", style="dim")
        if record.line_number:
            header.append(f":{record.line_number}", style="cyan")

    badges = Text()
    badges.append(f" ×{record.occurrence_count} ", style="bold")
    badges.append(f" {record.status.value} ", style=status_style)
    if confidence:
        badges.append(f" {confidence} ", style=conf_style)

    console.print()
    console.print(Panel(header, subtitle=badges, border_style="dim", padding=(0, 1)))

    # ── Meta ──────────────────────────────────────────────────────────────────
    meta = Table.grid(padding=(0, 4))
    meta.add_column(style="dim")
    meta.add_column()
    meta.add_row("fingerprint", record.fingerprint)
    meta.add_row("first seen",  str(record.first_seen)[:16] if record.first_seen else "—")
    meta.add_row("last seen",   str(record.last_seen)[:16]  if record.last_seen  else "—")
    if record.status == ErrorStatus.INACTIVE and record.resolved_at:
        meta.add_row("resolved at", str(record.resolved_at)[:16])
    console.print(meta)
    console.print()

    # ── Message ───────────────────────────────────────────────────────────────
    console.print(Panel(
        Text(record.message_template, style="white"),
        title="[dim]message[/dim]",
        border_style="dim",
        padding=(0, 1),
    ))

    # ── Hourly chart ──────────────────────────────────────────────────────────
    stats = db.get_hourly_stats(record.fingerprint, hours=hours)
    if stats:
        chart = _hourly_barchart(stats, hours=hours)
        # Colour the chart by trend
        counts = [s.count for s in stats]
        half = len(counts) // 2
        recent, prev = sum(counts[half:]), sum(counts[:half])
        chart_style = "red" if recent > prev * 1.2 else "green" if recent < prev * 0.8 else "blue"
        console.print(Panel(
            Text(chart, style=chart_style),
            title=f"[dim]occurrences · last {hours}h[/dim]",
            border_style="dim",
            padding=(0, 1),
        ))
    else:
        console.print(Panel(
            Text("No hourly data yet — populate with hourly.py", style="dim italic"),
            title="[dim]occurrences[/dim]",
            border_style="dim",
        ))

    # ── Analysis ──────────────────────────────────────────────────────────────
    if analysis:
        console.print(Panel(
            Columns([
                Panel(
                    Text(analysis.short_description, style="white"),
                    title="[dim]what happened[/dim]",
                    border_style="dim",
                    padding=(0, 1),
                    expand=True,
                ),
                Panel(
                    Text(analysis.root_cause, style="white"),
                    title="[dim]root cause[/dim]",
                    border_style="dim",
                    padding=(0, 1),
                    expand=True,
                ),
            ], equal=True, expand=True),
            title="[dim]analysis[/dim]",
            border_style="dim green",
            padding=(0, 0),
        ))
        console.print(Panel(
            Text(analysis.suggested_fix or "—", style="white"),
            title="[dim]suggested fix[/dim]",
            border_style="green",
            padding=(0, 1),
        ))
    else:
        console.print(Panel(
            Text("No analysis yet. Run digest.py to analyze.", style="dim italic"),
            title="[dim]analysis[/dim]",
            border_style="dim",
        ))

    # ── Traceback ─────────────────────────────────────────────────────────────
    if record.sample_traceback:
        console.print(Panel(
            Text(record.sample_traceback, style="dim"),
            title="[dim]sample traceback[/dim]",
            border_style="dim",
            padding=(0, 1),
        ))

    console.print()


if __name__ == "__main__":
    cli()
