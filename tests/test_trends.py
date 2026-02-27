from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from reporting.renderer import _build_sparkline_data
from storage.db import _truncate_to_hour
from storage.models import ErrorHourlyStat


def _make_stat(hours_ago: int, count: int) -> ErrorHourlyStat:
    hour = _truncate_to_hour(datetime.now(timezone.utc)) - timedelta(hours=hours_ago)
    return ErrorHourlyStat(fingerprint="abc", hour=hour, count=count)


class TestBuildSparklineData:
    def test_empty_stats_has_no_data(self):
        result = _build_sparkline_data([])
        assert result["has_data"] is False

    def test_nonzero_stats_has_data(self):
        result = _build_sparkline_data([_make_stat(1, 5)])
        assert result["has_data"] is True

    def test_produces_48_points(self):
        result = _build_sparkline_data([_make_stat(1, 10)])
        assert len(result["points"]) == 48

    def test_max_count_reflects_highest_hour(self):
        stats = [_make_stat(5, 10), _make_stat(3, 50), _make_stat(1, 20)]
        result = _build_sparkline_data(stats)
        assert result["max_count"] == 50

    def test_hourly_list_length_is_48(self):
        result = _build_sparkline_data([_make_stat(1, 5)])
        assert len(result["hourly"]) == 48

    def test_points_x_range(self):
        result = _build_sparkline_data([_make_stat(1, 5)])
        xs = [p[0] for p in result["points"]]
        assert xs[0] == 0.0
        assert xs[-1] == 100.0

    def test_points_y_within_range(self):
        result = _build_sparkline_data([_make_stat(1, 5)])
        for _, y in result["points"]:
            assert 0 <= y <= 100


class TestTrendDetection:
    def test_rising_trend(self):
        # Recent 6h much higher than previous 6h
        stats = (
            [_make_stat(i, 2) for i in range(12, 6, -1)] +
            [_make_stat(i, 20) for i in range(6, 0, -1)]
        )
        result = _build_sparkline_data(stats)
        assert result["trend"] == "rising"

    def test_falling_trend(self):
        # Recent 6h much lower than previous 6h
        stats = (
            [_make_stat(i, 20) for i in range(12, 6, -1)] +
            [_make_stat(i, 2) for i in range(6, 0, -1)]
        )
        result = _build_sparkline_data(stats)
        assert result["trend"] == "falling"

    def test_stable_trend(self):
        # Consistent counts across all hours
        stats = [_make_stat(i, 10) for i in range(12, 0, -1)]
        result = _build_sparkline_data(stats)
        assert result["trend"] == "stable"

    def test_all_zeros_is_stable(self):
        result = _build_sparkline_data([])
        assert result["trend"] == "stable"
