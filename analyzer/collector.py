import logging
import subprocess
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class CollectorError(Exception):
    pass


def collect_logs(
    compose_file: str,
    service_name: str,
    since: str = "1h",
) -> str:
    """
    Run `docker compose logs --since <since> <service>` and return raw output.

    Args:
        compose_file: Path to the docker compose file.
        service_name: The compose service name (e.g. "api").
        since: Docker-style duration string, e.g. "1h", "30m".

    Returns:
        Raw log output as a single string.

    Raises:
        CollectorError: If the subprocess fails.
    """
    cmd = [
        "docker", "compose",
        "-f", compose_file,
        "logs",
        "--no-log-prefix",   # strip the "api-1  |" prefix for cleaner parsing
        "--since", since,
        service_name,
    ]

    logger.info("Collecting logs: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise CollectorError(
            f"docker compose logs failed (exit {result.returncode}):\n{result.stderr}"
        )

    # stderr is where docker compose writes logs by default; stdout may be empty
    output = result.stdout or result.stderr
    line_count = output.count("\n")
    logger.info("Collected %d lines", line_count)
    return output
