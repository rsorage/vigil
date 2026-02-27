import pytest
from pathlib import Path
from unittest.mock import patch

from analyzer.code_reader import (
    _container_path_to_host,
    _is_app_path,
    read_code_context,
    read_context_for_error,
)

# Fake source file content — 30 lines, line 15 is the "error line"
FAKE_SOURCE = "\n".join(f"line {i} content" for i in range(1, 31))


class TestContainerPathToHost:
    def test_strips_container_prefix(self):
        with patch("analyzer.code_reader.config") as cfg:
            cfg.app_container_path = "/app"
            cfg.app_source_path = "/home/ubuntu/myapp"
            result = _container_path_to_host("/app/app/services/device.py")
            assert str(result) == "/home/ubuntu/myapp/app/services/device.py"

    def test_handles_nested_path(self):
        with patch("analyzer.code_reader.config") as cfg:
            cfg.app_container_path = "/app"
            cfg.app_source_path = "/home/ubuntu/myapp"
            result = _container_path_to_host("/app/app/trip_management/infra/repo.py")
            assert str(result) == "/home/ubuntu/myapp/app/trip_management/infra/repo.py"


class TestIsAppPath:
    def test_app_path_is_true(self):
        assert _is_app_path("/app/app/services/device.py") is True

    def test_venv_path_is_false(self):
        assert _is_app_path("/app/.venv/lib/python3.13/site-packages/pydantic/main.py") is False

    def test_site_packages_is_false(self):
        assert _is_app_path("/usr/local/lib/python3.12/dist-packages/sqlalchemy/orm.py") is False


class TestReadCodeContext:
    def test_returns_context_around_line(self, tmp_path):
        source = tmp_path / "device.py"
        source.write_text(FAKE_SOURCE)

        with patch("analyzer.code_reader.config") as cfg:
            cfg.app_container_path = "/app"
            cfg.app_source_path = str(tmp_path)
            cfg.app_container_path = "/app"

            result = read_code_context("/app/device.py", line_number=15, context_lines=3)

        assert result is not None
        assert ">>> " in result          # error line is marked
        assert "15" in result            # error line number present
        assert "line 15 content" in result

    def test_error_line_is_marked(self, tmp_path):
        source = tmp_path / "device.py"
        source.write_text(FAKE_SOURCE)

        with patch("analyzer.code_reader.config") as cfg:
            cfg.app_container_path = "/app"
            cfg.app_source_path = str(tmp_path)

            result = read_code_context("/app/device.py", line_number=15, context_lines=3)

        lines = result.splitlines()
        marked = [l for l in lines if ">>>" in l]
        assert len(marked) == 1
        assert "15" in marked[0]

    def test_includes_header_with_path(self, tmp_path):
        source = tmp_path / "device.py"
        source.write_text(FAKE_SOURCE)

        with patch("analyzer.code_reader.config") as cfg:
            cfg.app_container_path = "/app"
            cfg.app_source_path = str(tmp_path)

            result = read_code_context("/app/device.py", line_number=15, context_lines=3)

        assert result.startswith("# /app/device.py")

    def test_returns_none_for_missing_file(self, tmp_path):
        with patch("analyzer.code_reader.config") as cfg:
            cfg.app_container_path = "/app"
            cfg.app_source_path = str(tmp_path)

            result = read_code_context("/app/nonexistent.py", line_number=10)

        assert result is None

    def test_returns_none_for_venv_path(self, tmp_path):
        result = read_code_context(
            "/app/.venv/lib/python3.13/site-packages/pydantic/main.py",
            line_number=253,
        )
        assert result is None

    def test_clamps_context_at_file_boundaries(self, tmp_path):
        source = tmp_path / "short.py"
        source.write_text("line one\nline two\nline three\n")

        with patch("analyzer.code_reader.config") as cfg:
            cfg.app_container_path = "/app"
            cfg.app_source_path = str(tmp_path)

            # context_lines=20 but file only has 3 lines — should not crash
            result = read_code_context("/app/short.py", line_number=2, context_lines=20)

        assert result is not None
        assert "line one" in result
        assert "line three" in result


class TestReadContextForError:
    def test_returns_none_when_no_file_path(self):
        assert read_context_for_error(None, 42) is None

    def test_returns_none_when_no_line_number(self):
        assert read_context_for_error("/app/app/services/device.py", None) is None

    def test_returns_none_when_both_missing(self):
        assert read_context_for_error(None, None) is None
