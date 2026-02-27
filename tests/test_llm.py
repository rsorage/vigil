from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from llm.claude import ClaudeProvider, _build_user_message
from llm.ollama import OllamaProvider
from storage.models import ErrorAnalysis, ErrorRecord, ErrorStatus

NOW = datetime(2026, 2, 27, 10, 0, 0, tzinfo=timezone.utc)


def _make_error(
    with_traceback: bool = True,
    with_file: bool = True,
) -> ErrorRecord:
    return ErrorRecord(
        fingerprint="abc123",
        logger_name="app.trip_management.infrastructure.persistence.trip_repository_impl",
        message_template="Database error getting active trip: Multiple rows were found when one or none was required",
        sample_traceback=(
            "Traceback (most recent call last):\n"
            '  File "/app/app/trip_management/event_handlers/handler.py", line 289, in run\n'
            "    result = await repo.get_active(vin)\n"
            '  File "/app/app/trip_management/persistence/repo.py", line 111, in get_active\n'
            "    model = result.scalar_one_or_none()\n"
            "sqlalchemy.exc.MultipleResultsFound: Multiple rows were found"
        ) if with_traceback else None,
        file_path="/app/app/trip_management/persistence/repo.py" if with_file else None,
        line_number=111 if with_file else None,
        occurrence_count=96,
        first_seen=NOW,
        last_seen=NOW,
        status=ErrorStatus.NEW,
    )


def _make_tool_response(data: dict) -> MagicMock:
    """Build a mock Anthropic response that contains a tool_use block."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = data

    response = MagicMock()
    response.content = [tool_block]
    return response


class TestClaudeProviderAnalysis:
    def test_returns_error_analysis_on_success(self):
        expected = {
            "short_description": "Multiple active trips exist for same VIN",
            "root_cause": "Missing unique constraint allows duplicate active trips",
            "suggested_fix": "Add a partial unique index on (vin, tenant_id) where status = 'active'",
            "confidence": "high",
        }

        with patch("llm.claude.anthropic.Anthropic") as MockAnthropic:
            mock_client = MockAnthropic.return_value
            mock_client.messages.create.return_value = _make_tool_response(expected)

            with patch("llm.claude.config") as cfg:
                cfg.anthropic_api_key = "test-key"
                cfg.anthropic_model = "claude-sonnet-4-5"
                provider = ClaudeProvider()

            analysis = provider.analyze_error(_make_error(), code_context="def get_active(): ...")

        assert isinstance(analysis, ErrorAnalysis)
        assert analysis.short_description == expected["short_description"]
        assert analysis.root_cause == expected["root_cause"]
        assert analysis.suggested_fix == expected["suggested_fix"]
        assert analysis.confidence == "high"

    def test_falls_back_when_no_tool_block(self):
        response = MagicMock()
        response.content = []  # no tool_use block

        with patch("llm.claude.anthropic.Anthropic") as MockAnthropic:
            mock_client = MockAnthropic.return_value
            mock_client.messages.create.return_value = response

            with patch("llm.claude.config") as cfg:
                cfg.anthropic_api_key = "test-key"
                cfg.anthropic_model = "claude-sonnet-4-5"
                provider = ClaudeProvider()

            analysis = provider.analyze_error(_make_error(), code_context=None)

        assert analysis.confidence == "low"
        assert analysis.short_description == "Analysis unavailable"

    def test_works_without_code_context(self):
        expected = {
            "short_description": "Multiple active trips",
            "root_cause": "Duplicate rows in DB",
            "suggested_fix": "Add unique constraint",
            "confidence": "medium",
        }
        with patch("llm.claude.anthropic.Anthropic") as MockAnthropic:
            mock_client = MockAnthropic.return_value
            mock_client.messages.create.return_value = _make_tool_response(expected)

            with patch("llm.claude.config") as cfg:
                cfg.anthropic_api_key = "test-key"
                cfg.anthropic_model = "claude-sonnet-4-5"
                provider = ClaudeProvider()

            # No code context — error without a resolved file
            analysis = provider.analyze_error(_make_error(with_file=False), code_context=None)

        assert analysis.confidence == "medium"


class TestClaudeProviderPrompt:
    def test_message_includes_logger_name(self):
        error = _make_error()
        msg = _build_user_message(error, code_context=None)
        assert error.logger_name in msg

    def test_message_includes_traceback(self):
        error = _make_error(with_traceback=True)
        msg = _build_user_message(error, code_context=None)
        assert "Traceback" in msg

    def test_message_includes_code_context(self):
        error = _make_error()
        msg = _build_user_message(error, code_context="def get_active(): pass")
        assert "def get_active(): pass" in msg

    def test_message_notes_missing_context(self):
        error = _make_error(with_file=False, with_traceback=False)
        msg = _build_user_message(error, code_context=None)
        assert "No source code context" in msg

    def test_message_includes_occurrence_count(self):
        error = _make_error()
        msg = _build_user_message(error, code_context=None)
        assert "96" in msg


class TestOllamaProvider:
    def test_returns_fallback_on_connection_error(self):
        with patch("llm.ollama.urllib.request.urlopen") as mock_open:
            import urllib.error
            mock_open.side_effect = urllib.error.URLError("connection refused")

            with patch("llm.ollama.config") as cfg:
                cfg.ollama_base_url = "http://localhost:11434"
                cfg.ollama_model = "llama3"
                provider = OllamaProvider()

            analysis = provider.analyze_error(_make_error(), code_context=None)

        assert analysis.confidence == "low"
        assert "Ollama" in analysis.root_cause

    def test_returns_fallback_on_malformed_json(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"response": "not valid json {"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("llm.ollama.urllib.request.urlopen", return_value=mock_resp):
            with patch("llm.ollama.config") as cfg:
                cfg.ollama_base_url = "http://localhost:11434"
                cfg.ollama_model = "llama3"
                provider = OllamaProvider()

            analysis = provider.analyze_error(_make_error(), code_context=None)

        assert analysis.confidence == "low"

    def test_strips_markdown_fences_from_response(self):
        valid_json = '{"short_description":"x","root_cause":"y","suggested_fix":"z","confidence":"high"}'
        raw_response = f"```json\n{valid_json}\n```"

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"response": raw_response}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("llm.ollama.urllib.request.urlopen", return_value=mock_resp):
            with patch("llm.ollama.config") as cfg:
                cfg.ollama_base_url = "http://localhost:11434"
                cfg.ollama_model = "llama3"
                provider = OllamaProvider()

            analysis = provider.analyze_error(_make_error(), code_context=None)

        assert analysis.confidence == "high"
        assert analysis.short_description == "x"


import json  # noqa: E402 — needed by test above
