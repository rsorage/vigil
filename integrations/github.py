"""
GitHub integration — open and inspect issues from Vigil error records.

Uses urllib only; no third-party HTTP library required.
"""
import json
import urllib.request
import urllib.error
from typing import Optional

from storage.models import ErrorRecord


_API_BASE = "https://api.github.com"


# ── API calls ─────────────────────────────────────────────────────────────────

def open_issue(token: str, repo: str, title: str, body: str) -> str:
    """
    Create a GitHub issue and return its HTML URL.
    Raises RuntimeError on API errors.
    """
    url = f"{_API_BASE}/repos/{repo}/issues"
    payload = json.dumps({"title": title, "body": body}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers=_headers(token),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            return data["html_url"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"GitHub API error {e.code}: {body}") from e


def get_issue(token: str, issue_url: str) -> dict:
    """
    Fetch a single issue by its HTML URL and return the raw API response.
    Returns a dict with at least: number, title, state, html_url.
    """
    # Convert HTML URL → API URL
    # https://github.com/owner/repo/issues/123
    # → https://api.github.com/repos/owner/repo/issues/123
    api_url = issue_url.replace("https://github.com/", f"{_API_BASE}/repos/")
    req = urllib.request.Request(api_url, headers=_headers(token))
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub API error {e.code}: {e.read().decode()}") from e


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "vigil",
    }


# ── Issue template ────────────────────────────────────────────────────────────

def build_issue(record: ErrorRecord) -> tuple[str, str]:
    """
    Build (title, body) for a GitHub issue from an ErrorRecord.
    Works with or without LLM analysis.
    """
    analysis = record.analysis
    if isinstance(analysis, dict) and analysis:
        from storage.models import ErrorAnalysis
        analysis = ErrorAnalysis(**analysis)
    else:
        analysis = None

    title = (
        analysis.short_description
        if analysis
        else record.message_template.splitlines()[0][:120]
    )

    body = _build_body(record, analysis)
    return title, body


def _build_body(record: ErrorRecord, analysis) -> str:
    sections: list[str] = []

    # ── Analysis sections (only if available) ─────────────────────────────────
    if analysis:
        sections.append(_section("What happened", analysis.short_description))
        sections.append(_section("Root cause", analysis.root_cause))
        if analysis.suggested_fix:
            sections.append(_section("Suggested fix", analysis.suggested_fix))

    # ── Metadata table ────────────────────────────────────────────────────────
    rows = [
        ("Logger", f"`{record.logger_name}`"),
        ("Occurrences", str(record.occurrence_count)),
        ("First seen", str(record.first_seen)[:16] if record.first_seen else "—"),
        ("Last seen",  str(record.last_seen)[:16]  if record.last_seen  else "—"),
        ("Fingerprint", f"`{record.fingerprint}`"),
    ]
    if record.file_path:
        rows.insert(1, ("File", f"`{record.file_path}:{record.line_number}`"))

    table = "| Field | Value |\n|---|---|\n"
    table += "\n".join(f"| {k} | {v} |" for k, v in rows)
    sections.append(_section("Metadata", table))

    # ── Message ───────────────────────────────────────────────────────────────
    sections.append(_section(
        "Error message",
        f"```\n{record.message_template}\n```"
    ))

    # ── Traceback (collapsed) ─────────────────────────────────────────────────
    if record.sample_traceback:
        tb = (
            "<details>\n<summary>Sample traceback</summary>\n\n"
            f"```\n{record.sample_traceback}\n```\n\n</details>"
        )
        sections.append(tb)

    sections.append("---\n*Opened by [Vigil](https://github.com/rsorage/vigil)*")

    return "\n\n".join(sections)


def _section(title: str, content: str) -> str:
    return f"## {title}\n\n{content}"
