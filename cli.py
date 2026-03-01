"""
Vigil CLI — inspect and manage errors from the terminal.

Commands:
    vigil list-errors                    # list active errors
    vigil describe-error <prefix>        # full detail for one error
    vigil delete-error <prefix>          # hard-delete a record
    vigil open-issue <prefix>            # open a GitHub issue for an error
    vigil list-issues                    # list errors with GitHub issues
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
    blocks = " ▁▂▃▄▅▆▇█"
    if not counts or max(counts) == 0:
        return Text(blocks[0] * width, style="dim")
    peak = max(counts)
    if len(counts) > width:
        step = len(counts) / width
        counts = [counts[int(i * step)] for i in range(width)]
    chars = "".join(blocks[min(int(c / peak * 8), 8)] for c in counts)
    half = len(counts) // 2
    recent, prev = sum(counts[half:]), sum(counts[:half])
    style = "red" if recent > prev * 1.2 else "green" if recent < prev * 0.8 else "blue"
    return Text(chars, style=style)


def _hourly_barchart(stats: list, hours: int = 48, bar_width: int = 2) -> str:
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
    rows = []
    for row in range(chart_height, 0, -1):
        threshold = peak * row / chart_height
        rows.append("".join(("█" if c >= threshold else " ") * bar_width for c in counts))
    axis = "".join(
        (f"{h.hour:02d}" if h.hour % 6 == 0 else "  ")[:bar_width].ljust(bar_width)
        for h in hours_list
    )
    rows.append("─" * (len(counts) * bar_width))
    rows.append(axis.rstrip())
    rows.append(f"peak: {peak}  ·  last {hours}h")
    return "\n".join(rows)


def _resolve_fingerprint(db: Database, prefix: str) -> ErrorRecord | None:
    from sqlmodel import Session, select
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


# ── CLI root ──────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """Vigil — inspect and manage your error database from the terminal."""
    pass


# ── vigil list-errors ─────────────────────────────────────────────────────────

@cli.command("list-errors")
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

    fingerprints = [e.fingerprint for e in errors]
    stats_by_fp = db.get_hourly_stats_bulk(fingerprints, hours=24)
    has_issues = any(e.github_issue_url for e in errors)

    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold dim", pad_edge=False, expand=True)
    table.add_column("FINGERPRINT", style="dim", width=10, no_wrap=True)
    table.add_column("LOGGER", no_wrap=True, ratio=3)
    table.add_column("COUNT", justify="right", width=7)
    table.add_column("STATUS", width=10)
    table.add_column("CONFIDENCE", width=10)
    table.add_column("LAST SEEN", width=17)
    table.add_column("24H TREND", width=30, no_wrap=True)
    if has_issues:
        table.add_column("ISSUE", width=6)

    for e in errors:
        counts = _align_to_buckets(stats_by_fp.get(e.fingerprint, []), hours=24)
        spark = _sparkline(counts, width=28)
        confidence = e.analysis.get("confidence") if isinstance(e.analysis, dict) else (
            e.analysis.confidence if e.analysis else None
        )
        last_seen = e.last_seen.strftime("%m-%d %H:%M") if e.last_seen else "—"
        row_cells = [
            e.fingerprint[:8],
            e.logger_name,
            str(e.occurrence_count),
            Text(e.status.value, style=_status_style(e.status.value)),
            Text(confidence or "—", style=_confidence_style(confidence or "")),
            last_seen,
            spark,
        ]
        if has_issues:
            if e.github_issue_url:
                num = e.github_issue_url.rstrip("/").split("/")[-1]
                row_cells.append(Text(f"#{num}", style="blue"))
            else:
                row_cells.append(Text("—", style="dim"))
        table.add_row(*row_cells)

    console.print()
    console.print(table)
    console.print(
        f"  [dim]{len(errors)} error{'s' if len(errors) != 1 else ''}  ·  "
        f"[/dim][bold]vigil describe-error <prefix>[/bold][dim] for details[/dim]"
    )
    console.print()


# ── vigil describe-error ──────────────────────────────────────────────────────

@cli.command("describe-error")
@click.argument("prefix")
@click.option("--hours", default=48, show_default=True, help="Hours of history to show in chart.")
def describe_error(prefix: str, hours: int):
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

    header = Text()
    header.append(record.logger_name, style="bold yellow")
    if record.file_path:
        header.append(f"\n{record.file_path}", style="dim")
        if record.line_number:
            header.append(f":{record.line_number}", style="cyan")

    badges = Text()
    badges.append(f" ×{record.occurrence_count} ", style="bold")
    badges.append(f" {record.status.value} ", style=_status_style(record.status.value))
    if confidence:
        badges.append(f" {confidence} ", style=_confidence_style(confidence))

    console.print()
    console.print(Panel(header, subtitle=badges, border_style="dim", padding=(0, 1)))

    meta = Table.grid(padding=(0, 4))
    meta.add_column(style="dim")
    meta.add_column()
    meta.add_row("fingerprint", record.fingerprint)
    meta.add_row("first seen",  str(record.first_seen)[:16] if record.first_seen else "—")
    meta.add_row("last seen",   str(record.last_seen)[:16]  if record.last_seen  else "—")
    if record.status == ErrorStatus.INACTIVE and record.resolved_at:
        meta.add_row("resolved at", str(record.resolved_at)[:16])
    if record.github_issue_url:
        meta.add_row("github issue", record.github_issue_url)
    console.print(meta)
    console.print()

    console.print(Panel(
        Text(record.message_template, style="white"),
        title="[dim]message[/dim]", border_style="dim", padding=(0, 1),
    ))

    stats = db.get_hourly_stats(record.fingerprint, hours=hours)
    if stats:
        chart = _hourly_barchart(stats, hours=hours)
        counts = [s.count for s in stats]
        half = len(counts) // 2
        recent, prev = sum(counts[half:]), sum(counts[:half])
        chart_style = "red" if recent > prev * 1.2 else "green" if recent < prev * 0.8 else "blue"
        console.print(Panel(
            Text(chart, style=chart_style),
            title=f"[dim]occurrences · last {hours}h[/dim]",
            border_style="dim", padding=(0, 1),
        ))
    else:
        console.print(Panel(
            Text("No hourly data yet — populate with hourly.py", style="dim italic"),
            title="[dim]occurrences[/dim]", border_style="dim",
        ))

    if analysis:
        console.print(Panel(
            Columns([
                Panel(Text(analysis.short_description, style="white"),
                      title="[dim]what happened[/dim]", border_style="dim", padding=(0, 1), expand=True),
                Panel(Text(analysis.root_cause, style="white"),
                      title="[dim]root cause[/dim]", border_style="dim", padding=(0, 1), expand=True),
            ], equal=True, expand=True),
            title="[dim]analysis[/dim]", border_style="dim green", padding=(0, 0),
        ))
        console.print(Panel(
            Text(analysis.suggested_fix or "—", style="white"),
            title="[dim]suggested fix[/dim]", border_style="green", padding=(0, 1),
        ))
    else:
        console.print(Panel(
            Text("No analysis yet. Run digest.py to analyze.", style="dim italic"),
            title="[dim]analysis[/dim]", border_style="dim",
        ))

    if record.sample_traceback:
        console.print(Panel(
            Text(record.sample_traceback, style="dim"),
            title="[dim]sample traceback[/dim]", border_style="dim", padding=(0, 1),
        ))

    console.print()


# ── vigil delete-error ────────────────────────────────────────────────────────

@cli.command("delete-error")
@click.argument("prefix")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
def delete_error(prefix: str, yes: bool):
    """Hard-delete an error record and its hourly stats."""
    db = _get_db()
    record = _resolve_fingerprint(db, prefix)
    if record is None:
        return

    console.print()
    console.print(f"  [dim]fingerprint[/dim]  {record.fingerprint}")
    console.print(f"  [dim]logger[/dim]       {record.logger_name}")
    console.print(f"  [dim]occurrences[/dim]  {record.occurrence_count}")
    console.print()

    if not yes:
        click.confirm("Permanently delete this error record?", default=False, abort=True)

    db.delete_error(record.fingerprint)
    console.print(f"[green]✓[/green] Deleted [dim]{record.fingerprint}[/dim]")
    console.print()


# ── vigil open-issue ──────────────────────────────────────────────────────────

@cli.command("open-issue")
@click.argument("prefix")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
def open_issue(prefix: str, yes: bool):
    """Open a GitHub issue for an error by fingerprint prefix."""
    from integrations.github import build_issue, open_issue as gh_open_issue

    if not config.github_token or not config.github_repo:
        console.print(
            "[red]Error:[/red] GITHUB_TOKEN and GITHUB_REPO must be set in .env "
            "to use GitHub integration."
        )
        return

    db = _get_db()
    record = _resolve_fingerprint(db, prefix)
    if record is None:
        return

    if record.github_issue_url:
        console.print(
            f"[yellow]An issue already exists for this error:[/yellow] "
            f"{record.github_issue_url}"
        )
        return

    title, body = build_issue(record)

    console.print()
    console.print(f"  [dim]repo[/dim]   {config.github_repo}")
    console.print(f"  [dim]title[/dim]  {title}")
    console.print()

    if not yes:
        click.confirm(f"Open issue on {config.github_repo}?", default=False, abort=True)

    with console.status("Opening issue..."):
        try:
            url = gh_open_issue(config.github_token, config.github_repo, title, body)
        except RuntimeError as e:
            console.print(f"[red]Failed to open issue:[/red] {e}")
            return

    db.save_github_issue_url(record.fingerprint, url)
    console.print(f"[green]✓[/green] Issue opened: [bold]{url}[/bold]")
    console.print()


# ── vigil list-issues ─────────────────────────────────────────────────────────

@cli.command("list-issues")
def list_issues():
    """List all errors that have an associated GitHub issue."""
    from integrations.github import get_issue

    if not config.github_token:
        console.print(
            "[red]Error:[/red] GITHUB_TOKEN must be set in .env "
            "to use GitHub integration."
        )
        return

    db = _get_db()
    records = db.get_errors_with_issues()

    if not records:
        console.print("[dim]No GitHub issues opened from Vigil yet.[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold dim", pad_edge=False, expand=True)
    table.add_column("FINGERPRINT", style="dim", width=10, no_wrap=True)
    table.add_column("TITLE", ratio=3)
    table.add_column("COUNT", justify="right", width=7)
    table.add_column("STATE", width=8)
    table.add_column("URL", ratio=2)

    for record in records:
        title, state, state_style = "—", "?", "dim"
        try:
            issue = get_issue(config.github_token, record.github_issue_url)
            title = issue.get("title", "—")[:80]
            state = issue.get("state", "?")
            state_style = "green" if state == "open" else "dim"
        except RuntimeError:
            state, state_style = "error", "red"

        table.add_row(
            record.fingerprint[:8],
            title,
            str(record.occurrence_count),
            Text(state, style=state_style),
            record.github_issue_url or "—",
        )

    console.print()
    console.print(table)
    console.print(f"  [dim]{len(records)} issue{'s' if len(records) != 1 else ''}[/dim]")
    console.print()


if __name__ == "__main__":
    cli()
