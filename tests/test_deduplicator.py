from datetime import datetime, timezone
from analyzer.deduplicator import deduplicate, _normalize_message, _fingerprint
from storage.models import LogEvent

T1 = datetime(2026, 2, 24, 13, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 2, 24, 13, 30, 0, tzinfo=timezone.utc)
T3 = datetime(2026, 2, 24, 14, 0, 0, tzinfo=timezone.utc)


def _vitals_event(ts: datetime, cpu_value: str = "147.906") -> LogEvent:
    return LogEvent(
        timestamp=ts,
        logger_name="app.device_management.infrastructure.mqtt.device_vitals_processor",
        level="ERROR",
        message=f"Error processing device vitals: 1 validation error for DeviceVitalsInput\ncpu_usage\n  Input should be less than or equal to 100 [type=less_than_equal, input_value={cpu_value}, input_type=float]",
        file_path="/app/app/schemas/device_vitals.py",
        line_number=30,
        traceback="Traceback...\n  File \"/app/app/schemas/device_vitals.py\", line 30",
    )


def _webhook_event(ts: datetime) -> LogEvent:
    return LogEvent(
        timestamp=ts,
        logger_name="app.routers.emqx.webhooks",
        level="ERROR",
        message="Unhandled exception in webhook handler",
        file_path="/app/app/services/webhook_service.py",
        line_number=88,
        traceback="Traceback...\n  File \"/app/app/services/webhook_service.py\", line 88",
    )


class TestDeduplicateGrouping:
    def test_identical_errors_collapsed(self):
        events = [_vitals_event(T1), _vitals_event(T2), _vitals_event(T3)]
        records = deduplicate(events)
        assert len(records) == 1
        assert records[0].occurrence_count == 3

    def test_different_errors_kept_separate(self):
        events = [_vitals_event(T1), _webhook_event(T2)]
        records = deduplicate(events)
        assert len(records) == 2

    def test_same_error_different_cpu_value_still_deduped(self):
        # Variable values like 147.906 vs 200.1 should not create separate records
        events = [_vitals_event(T1, "147.906"), _vitals_event(T2, "200.1")]
        records = deduplicate(events)
        assert len(records) == 1

    def test_empty_input(self):
        assert deduplicate([]) == []


class TestDeduplicateTimestamps:
    def test_first_seen_is_earliest(self):
        events = [_vitals_event(T2), _vitals_event(T1), _vitals_event(T3)]
        records = deduplicate(events)
        assert records[0].first_seen == T1

    def test_last_seen_is_latest(self):
        events = [_vitals_event(T1), _vitals_event(T3), _vitals_event(T2)]
        records = deduplicate(events)
        assert records[0].last_seen == T3


class TestDeduplicateFields:
    def test_sample_traceback_preserved(self):
        records = deduplicate([_vitals_event(T1)])
        assert records[0].sample_traceback is not None
        assert "Traceback" in records[0].sample_traceback

    def test_file_path_preserved(self):
        records = deduplicate([_vitals_event(T1)])
        assert records[0].file_path == "/app/app/schemas/device_vitals.py"

    def test_line_number_preserved(self):
        records = deduplicate([_vitals_event(T1)])
        assert records[0].line_number == 30

    def test_logger_name_preserved(self):
        records = deduplicate([_vitals_event(T1)])
        assert "device_vitals_processor" in records[0].logger_name


class TestNormalization:
    def test_strips_tenant_id(self):
        msg = "Error for tenant_id=ten_psk6F3AvIyJm device not found"
        result = _normalize_message(msg)
        assert "ten_psk6F3AvIyJm" not in result
        assert "tenant_id=<id>" in result

    def test_strips_device_id(self):
        msg = "device_id=dev_QMsPZFBJpghE failed to connect"
        result = _normalize_message(msg)
        assert "dev_QMsPZFBJpghE" not in result

    def test_strips_ip_address(self):
        msg = 'Request from 172.24.0.5:52400 failed'
        result = _normalize_message(msg)
        assert "172.24.0.5" not in result

    def test_strips_float_values(self):
        msg = "input_value=147.906 exceeds limit"
        result = _normalize_message(msg)
        assert "147.906" not in result

    def test_strips_version(self):
        msg = "firmware version 1.8.0 not supported"
        result = _normalize_message(msg)
        assert "1.8.0" not in result

    def test_preserves_error_type(self):
        msg = "Error processing device vitals: 1 validation error for DeviceVitalsInput"
        result = _normalize_message(msg)
        assert "validation error for DeviceVitalsInput" in result


class TestFingerprint:
    def test_same_inputs_same_fingerprint(self):
        fp1 = _fingerprint("app.service", "some error", "/app/file.py", 42)
        fp2 = _fingerprint("app.service", "some error", "/app/file.py", 42)
        assert fp1 == fp2

    def test_different_logger_different_fingerprint(self):
        fp1 = _fingerprint("app.service_a", "some error", "/app/file.py", 42)
        fp2 = _fingerprint("app.service_b", "some error", "/app/file.py", 42)
        assert fp1 != fp2

    def test_different_line_different_fingerprint(self):
        fp1 = _fingerprint("app.service", "some error", "/app/file.py", 42)
        fp2 = _fingerprint("app.service", "some error", "/app/file.py", 99)
        assert fp1 != fp2

    def test_no_traceback_still_fingerprints(self):
        fp = _fingerprint("app.service", "generic error message", None, None)
        assert len(fp) == 16
