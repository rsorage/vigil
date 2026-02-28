# Contributing to Vigil

Thanks for your interest. Vigil is a personal project I'm actively using in production — contributions are welcome, but please read this first so we're on the same page.

## The honest context

This project is maintained in my spare time, which is limited. I'll do my best to review PRs and respond to issues, but response times may vary. I'd rather set that expectation upfront than leave you waiting.

## What I'm most interested in

- Bug fixes, especially anything affecting reliability of the hourly collection or report rendering
- Improvements to the LLM prompt quality or analysis output
- Support for additional Docker log formats or Python logging patterns
- Better documentation and setup guides

## What to check before opening a PR

- There's no open issue or PR already covering the same thing
- The change is focused — one thing per PR makes review much easier
- Existing tests still pass (`uv run pytest tests/ -v`)
- If you're adding behaviour, add a test for it

## How to set up a dev environment

```bash
git clone https://github.com/your-username/vigil.git
cd vigil
cp .env.example .env   # fill in at minimum DOCKER_COMPOSE_FILE and APP_SOURCE_PATH
uv sync
uv run pytest tests/ -v
```

You don't need an Anthropic API key to run the test suite — LLM calls are mocked in tests.

## Opening an issue

For bugs, include:
- What you ran
- What you expected to happen
- What actually happened (paste the traceback if there is one)
- Your Python version and OS

For feature requests, explain the use case rather than just the implementation. I'm more likely to engage with "I have X problem and here's how I'm thinking about it" than "add Y feature."

## Code style

- Standard Python — `ruff` for linting if you want to check locally
- Type hints on all new functions
- Keep it simple; this codebase values readability over cleverness
