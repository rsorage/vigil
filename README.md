# Vigil

> Watches your backend so you don't have to.

Vigil is a lightweight AIOps pipeline that runs alongside your production stack. Every hour it collects your Docker container logs, extracts and deduplicates errors, and at the end of the day delivers an HTML digest with LLM-powered root cause analysis and fix suggestions for every unique error it found.

## How it works

```mermaid
flowchart LR
    A([Docker logs]) --> B[Parse]
    B --> C[Deduplicate]
    C --> D[(SQLite)]

    D -->|daily| E[Code context]
    E --> F[LLM analysis]
    F --> G([HTML digest])
```

- **Hourly**: Collects the last hour of logs from a Docker Compose service, parses multiline entries (including tracebacks), deduplicates errors by fingerprint, and persists them with occurrence counts and timestamps.
- **Daily**: Runs LLM analysis on any new unique errors — reading the relevant source files for context — and renders a styled HTML report.
- **Lifecycle tracking**: Each error has a status (`new` → `analyzed` → `inactive`). Errors not seen in 48 hours are automatically marked inactive. If they reappear, they're re-queued for analysis.

## Stack

- **Python 3.11+** with [SQLModel](https://sqlmodel.tiangolo.com/) (SQLite) for persistence
- **Anthropic Claude API** for root cause analysis (Ollama/local models also supported)
- **Jinja2** for HTML report rendering
- Runs as plain Python processes scheduled via cron — no extra infrastructure required

## Project structure

```
vigil/
├── analyzer/
│   ├── collector.py       # docker compose logs → raw text
│   ├── parser.py          # raw text → LogEvent dataclasses
│   ├── deduplicator.py    # fingerprinting + normalization
│   ├── code_reader.py     # traceback path → code context window
│   └── state_manager.py   # inactive transition logic
├── llm/
│   ├── base.py            # abstract LLMProvider interface
│   ├── claude.py          # Anthropic implementation
│   └── ollama.py          # Ollama implementation
├── storage/
│   ├── models.py          # SQLModel table + dataclasses
│   └── db.py              # database access layer
├── reporting/
│   ├── renderer.py        # Jinja2 → HTML report
│   └── templates/
├── reports/               # generated daily digests (YYYY-MM-DD.html)
├── config.py              # pydantic-settings, .env-driven
├── hourly.py              # cron entry: collect → parse → dedup → persist
└── digest.py              # cron entry: analyze → render report
```

## Setup

**Requirements**: Python 3.11+, Docker Compose, access to the target service's compose file and source code.

```bash
git clone https://github.com/your-username/vigil.git
cd vigil
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env
# Edit .env with your paths, API key, and service name
```

## Configuration

All configuration is via `.env`. Key settings:

| Variable | Description | Default |
|---|---|---|
| `LLM_PROVIDER` | `claude` or `ollama` | `claude` |
| `ANTHROPIC_API_KEY` | Your Anthropic API key | — |
| `DOCKER_COMPOSE_FILE` | Path to your `docker-compose.prod.yml` | — |
| `DOCKER_SERVICE_NAME` | Compose service to watch | `api` |
| `APP_SOURCE_PATH` | Host path to your app source code | — |
| `APP_CONTAINER_PATH` | Container path prefix to strip | `/app` |
| `ERROR_INACTIVE_AFTER_HOURS` | Hours before unseen errors go inactive | `48` |
| `REPORTS_DIR` | Where to write HTML reports | `reports/` |

## Cron setup

```bash
# Collect and deduplicate every hour
0 * * * * cd /home/ubuntu/vigil && .venv/bin/python hourly.py >> logs/hourly.log 2>&1

# Run LLM analysis and generate daily digest at 6pm
0 18 * * * cd /home/ubuntu/vigil && .venv/bin/python digest.py >> logs/digest.log 2>&1
```

## Running tests

```bash
python -m pytest tests/ -v
```

## Roadmap

- [ ] GitHub integration — auto-open issues for new errors
- [ ] PR generation — LLM proposes a fix, opens a draft PR for review
- [ ] Per-component LLM configuration — use different models for different analysis tasks
- [ ] Dockerized deployment
- [ ] Web UI for browsing historical reports
