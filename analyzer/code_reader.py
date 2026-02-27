import logging
from pathlib import Path
from typing import Optional

from config import config

logger = logging.getLogger(__name__)

# Lines of context to include above and below the error line
CONTEXT_LINES = 20


def _container_path_to_host(container_path: str) -> Path:
    """
    Map a container path to its equivalent on the host filesystem.

    e.g. /app/app/services/device.py
      →  /home/ubuntu/data-fleet-device-hub/app/services/device.py
    """
    prefix = config.app_container_path.rstrip("/") + "/"
    if container_path.startswith(prefix):
        relative = container_path[len(prefix):]
    else:
        relative = container_path.lstrip("/")

    return Path(config.app_source_path) / relative


def _is_app_path(container_path: str) -> bool:
    """Return True if the path belongs to app source, not venv/stdlib."""
    fragments = (".venv", "site-packages", "dist-packages")
    return not any(f in container_path for f in fragments)


def read_code_context(
    file_path: str,
    line_number: int,
    context_lines: int = CONTEXT_LINES,
) -> Optional[str]:
    """
    Read a window of source code around the error line.

    Returns a formatted string with line numbers, or None if the file
    cannot be found or the path is inside a venv/library.

    Args:
        file_path:     Container-absolute path, e.g. /app/app/services/foo.py
        line_number:   1-based line number of the error
        context_lines: Number of lines to include above and below

    Returns:
        Formatted code snippet with line numbers, or None.
    """
    if not _is_app_path(file_path):
        logger.debug("Skipping non-app path: %s", file_path)
        return None

    host_path = _container_path_to_host(file_path)

    if not host_path.exists():
        logger.warning("Source file not found on host: %s (mapped from %s)", host_path, file_path)
        return None

    try:
        lines = host_path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        logger.warning("Could not read %s: %s", host_path, e)
        return None

    total = len(lines)
    start = max(0, line_number - context_lines - 1)   # convert to 0-based
    end = min(total, line_number + context_lines)      # exclusive

    snippet_lines = []
    for i, line in enumerate(lines[start:end], start=start + 1):
        marker = ">>>" if i == line_number else "   "
        snippet_lines.append(f"{marker} {i:4d} | {line}")

    header = f"# {file_path} (lines {start + 1}–{end})\n"
    return header + "\n".join(snippet_lines)


def read_context_for_error(
    file_path: Optional[str],
    line_number: Optional[int],
) -> Optional[str]:
    """
    Convenience wrapper used by the LLM layer.
    Returns None gracefully if file_path or line_number are missing.
    """
    if not file_path or not line_number:
        return None
    return read_code_context(file_path, line_number)
