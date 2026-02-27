import pytest
from datetime import datetime, timezone
from analyzer.parser import parse_logs, filter_errors

# Realistic sample matching your actual log format, including:
# - plain INFO lines
# - a multi-line ERROR with Pydantic validation detail + traceback
# - an ERROR with only a traceback (no pre-traceback detail)
# - a WARNING
SAMPLE_LOGS = """\
2026-02-24 13:11:11 - uvicorn.access - INFO - 127.0.0.1:50658 - "GET /health HTTP/1.1" 200
2026-02-24 13:11:14 - app.services.device_auth - INFO - tenant_id=ten_psk6F3AvIyJm device_id=dev_QMsPZFBJpghE Device authenticated successfully
2026-02-24 13:11:14 - app.device_management.infrastructure.mqtt.device_vitals_processor - ERROR - Error processing device vitals: 1 validation error for DeviceVitalsInput
cpu_usage
  Input should be less than or equal to 100 [type=less_than_equal, input_value=147.906, input_type=float]
    For further information visit https://errors.pydantic.dev/2.11/v/less_than_equal
Traceback (most recent call last):
  File "/app/app/device_management/infrastructure/mqtt/device_vitals_processor.py", line 100, in process
    device_vitals = DeviceVitalsInput.from_compact_payload(vitals_data)
  File "/app/app/device_management/infrastructure/mqtt/device_vitals_processor.py", line 56, in from_compact_payload
    return cls(**payload)
  File "/app/app/schemas/device_vitals.py", line 30, in __init__
    raise ValueError("validation error")
ValueError: validation error
2026-02-24 13:11:15 - app.services.devices - WARNING - tenant_id=ten_abc Device metadata update skipped: missing fields
2026-02-24 13:11:16 - app.routers.emqx.webhooks - ERROR - Unhandled exception in webhook handler
Traceback (most recent call last):
  File "/app/app/routers/emqx/webhooks.py", line 45, in handle_webhook
    result = await processor.process(payload)
  File "/app/app/services/webhook_service.py", line 88, in process
    raise RuntimeError("connection timeout")
RuntimeError: connection timeout
2026-02-24 13:11:17 - uvicorn.access - INFO - 172.24.0.5:52400 - "POST /emqx/auth HTTP/1.1" 200
"""

# Same logs but with the docker compose prefix still present
SAMPLE_LOGS_WITH_PREFIX = """\
api-1  | 2026-02-24 13:11:11 - uvicorn.access - INFO - 127.0.0.1:50658 - "GET /health HTTP/1.1" 200
api-1  | 2026-02-24 13:11:14 - app.services.device_auth - INFO - tenant_id=ten_abc Device authenticated successfully
api-1  | 2026-02-24 13:11:14 - app.device_management.infrastructure.mqtt.device_vitals_processor - ERROR - Error processing device vitals: 1 validation error for DeviceVitalsInput
api-1  | cpu_usage
api-1  |   Input should be less than or equal to 100 [type=less_than_equal, input_value=147.906, input_type=float]
api-1  | Traceback (most recent call last):
api-1  |   File "/app/app/device_management/infrastructure/mqtt/device_vitals_processor.py", line 100, in process
api-1  |     device_vitals = DeviceVitalsInput.from_compact_payload(vitals_data)
api-1  | ValueError: validation error
api-1  | 2026-02-24 13:11:17 - uvicorn.access - INFO - 172.24.0.5:52400 - "POST /emqx/auth HTTP/1.1" 200
"""


class TestParseLogsCount:
    def test_correct_event_count(self):
        events = parse_logs(SAMPLE_LOGS)
        assert len(events) == 6

    def test_correct_error_count(self):
        events = parse_logs(SAMPLE_LOGS)
        errors = [e for e in events if e.level == "ERROR"]
        assert len(errors) == 2

    def test_correct_info_count(self):
        events = parse_logs(SAMPLE_LOGS)
        infos = [e for e in events if e.level == "INFO"]
        assert len(infos) == 3

    def test_warning_parsed(self):
        events = parse_logs(SAMPLE_LOGS)
        warnings = [e for e in events if e.level == "WARNING"]
        assert len(warnings) == 1


class TestParseLogsFields:
    def test_timestamp_parsed(self):
        events = parse_logs(SAMPLE_LOGS)
        assert events[0].timestamp == datetime(2026, 2, 24, 13, 11, 11, tzinfo=timezone.utc)

    def test_logger_name_parsed(self):
        events = parse_logs(SAMPLE_LOGS)
        assert events[0].logger_name == "uvicorn.access"

    def test_level_parsed(self):
        events = parse_logs(SAMPLE_LOGS)
        assert events[0].level == "INFO"

    def test_message_parsed(self):
        events = parse_logs(SAMPLE_LOGS)
        assert "200" in events[0].message


class TestMultilineError:
    def setup_method(self):
        events = parse_logs(SAMPLE_LOGS)
        self.error = next(e for e in events if e.level == "ERROR" and "vitals" in e.message)

    def test_traceback_captured(self):
        assert self.error.traceback is not None
        assert "Traceback (most recent call last)" in self.error.traceback

    def test_innermost_file_path_extracted(self):
        assert self.error.file_path == "/app/app/schemas/device_vitals.py"

    def test_line_number_extracted(self):
        assert self.error.line_number == 30

    def test_pre_traceback_detail_in_message(self):
        assert "cpu_usage" in self.error.message


class TestSimpleTracebackError:
    def setup_method(self):
        events = parse_logs(SAMPLE_LOGS)
        self.error = next(e for e in events if "webhook" in e.message.lower())

    def test_traceback_captured(self):
        assert self.error.traceback is not None

    def test_file_path_extracted(self):
        assert self.error.file_path == "/app/app/services/webhook_service.py"

    def test_line_number_extracted(self):
        assert self.error.line_number == 88


class TestDockerPrefix:
    def test_prefix_stripped_correctly(self):
        events = parse_logs(SAMPLE_LOGS_WITH_PREFIX)
        assert len(events) == 4  # 2 INFO + 1 ERROR + 1 INFO

    def test_fields_intact_after_prefix_strip(self):
        events = parse_logs(SAMPLE_LOGS_WITH_PREFIX)
        error = next(e for e in events if e.level == "ERROR")
        assert error.traceback is not None
        assert error.file_path == "/app/app/device_management/infrastructure/mqtt/device_vitals_processor.py"
        assert error.line_number == 100


class TestFilterErrors:
    def test_returns_only_errors(self):
        events = parse_logs(SAMPLE_LOGS)
        errors = filter_errors(events)
        assert all(e.level in ("ERROR", "CRITICAL") for e in errors)
        assert len(errors) == 2

    def test_empty_input(self):
        assert filter_errors([]) == []

    def test_no_errors_in_input(self):
        info_only = "2026-02-24 13:11:11 - uvicorn.access - INFO - all good\n"
        events = parse_logs(info_only)
        assert filter_errors(events) == []
