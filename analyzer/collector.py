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
    logger.info("Collected %d lines from %s", line_count, service_name)
    return output


def collect_all_logs(
    compose_file: str,
    services: list[str],
    since: str = "1h",
) -> dict[str, str]:
    """
    Collect logs for several compose services, one `docker compose logs` call
    each, and return them keyed by service name.

    Calling per service (rather than passing them all at once) keeps
    --no-log-prefix usable: the service each line came from is whatever we
    asked for, so it never has to be recovered from the log text.

    A service that fails to collect is logged and skipped — one broken
    container should not cost us the others.

    Raises:
        CollectorError: If every service failed, or if none were configured.
    """
    if not services:
        raise CollectorError("No services configured — set DOCKER_SERVICES in .env")

    collected: dict[str, str] = {}
    failures: list[str] = []

    for service in services:
        try:
            collected[service] = collect_logs(compose_file, service, since)
        except CollectorError as e:
            logger.error("Skipping service %r: %s", service, e)
            failures.append(service)

    if not collected:
        raise CollectorError(
            f"Log collection failed for every service ({', '.join(failures)})"
        )

    return collected
