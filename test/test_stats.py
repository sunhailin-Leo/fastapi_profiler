"""Unit tests for fastapi_profiler.stats module."""

import asyncio

import pytest

from fastapi_profiler.stats import ProfileRecord, RouteStats, StatsCollector

# ---------------------------------------------------------------------------
# ProfileRecord
# ---------------------------------------------------------------------------


class TestProfileRecord:
    def test_create_generates_request_id(self):
        record = ProfileRecord.create(
            path="/test", method="GET", status_code=200, duration_ms=10.0
        )
        assert record.request_id
        assert len(record.request_id) == 36  # UUID4 format

    def test_create_generates_timestamp(self):
        record = ProfileRecord.create(
            path="/test", method="GET", status_code=200, duration_ms=10.0
        )
        assert "T" in record.timestamp  # ISO 8601

    def test_create_stores_fields(self):
        record = ProfileRecord.create(
            path="/api/users",
            method="POST",
            status_code=201,
            duration_ms=42.5,
            profile_output="some profile text",
        )
        assert record.path == "/api/users"
        assert record.method == "POST"
        assert record.status_code == 201
        assert record.duration_ms == 42.5
        assert record.profile_output == "some profile text"

    def test_create_profile_output_defaults_to_none(self):
        record = ProfileRecord.create(
            path="/test", method="GET", status_code=200, duration_ms=5.0
        )
        assert record.profile_output is None

    def test_two_records_have_different_request_ids(self):
        record_a = ProfileRecord.create("/a", "GET", 200, 1.0)
        record_b = ProfileRecord.create("/b", "GET", 200, 1.0)
        assert record_a.request_id != record_b.request_id


# ---------------------------------------------------------------------------
# RouteStats
# ---------------------------------------------------------------------------

class TestRouteStats:
    def _make_stats(self) -> RouteStats:
        return RouteStats(path="/test", method="GET")

    def test_initial_state(self):
        stats = self._make_stats()
        assert stats.count == 0
        assert stats.error_count == 0
        assert stats.total_duration_ms == 0.0
        assert stats.avg_duration_ms == 0.0

    def test_record_increments_count(self):
        stats = self._make_stats()
        stats.record(100.0, 200)
        assert stats.count == 1

    def test_record_accumulates_duration(self):
        stats = self._make_stats()
        stats.record(100.0, 200)
        stats.record(200.0, 200)
        assert stats.total_duration_ms == 300.0

    def test_avg_duration_ms(self):
        stats = self._make_stats()
        stats.record(100.0, 200)
        stats.record(200.0, 200)
        assert stats.avg_duration_ms == 150.0

    def test_max_and_min_duration(self):
        stats = self._make_stats()
        stats.record(50.0, 200)
        stats.record(200.0, 200)
        stats.record(10.0, 200)
        assert stats.max_duration_ms == 200.0
        assert stats.min_duration_ms == 10.0

    def test_error_count_4xx(self):
        stats = self._make_stats()
        stats.record(10.0, 404)
        assert stats.error_count == 1

    def test_error_count_5xx(self):
        stats = self._make_stats()
        stats.record(10.0, 500)
        assert stats.error_count == 1

    def test_success_does_not_increment_error_count(self):
        stats = self._make_stats()
        stats.record(10.0, 200)
        stats.record(10.0, 201)
        assert stats.error_count == 0

    def test_p95_single_sample_returns_max(self):
        stats = self._make_stats()
        stats.record(100.0, 200)
        assert stats.p95_duration_ms == 100.0

    def test_p99_single_sample_returns_max(self):
        stats = self._make_stats()
        stats.record(100.0, 200)
        assert stats.p99_duration_ms == 100.0

    def test_p95_multiple_samples(self):
        stats = self._make_stats()
        for value in range(1, 101):  # 1..100
            stats.record(float(value), 200)
        # p95 of 100 samples: index = int(100 * 0.95) = 95 → sorted[95] = 96
        assert stats.p95_duration_ms == 96.0

    def test_p99_multiple_samples(self):
        stats = self._make_stats()
        for value in range(1, 101):
            stats.record(float(value), 200)
        # p99: index = int(100 * 0.99) = 99 → sorted[99] = 100
        assert stats.p99_duration_ms == 100.0

    def test_to_dict_keys(self):
        stats = self._make_stats()
        stats.record(50.0, 200)
        result = stats.to_dict()
        expected_keys = {
            "path", "method", "count", "error_count",
            "total_duration_ms", "max_duration_ms", "min_duration_ms",
            "avg_duration_ms", "p95_duration_ms", "p99_duration_ms",
        }
        assert set(result.keys()) == expected_keys

    def test_to_dict_no_samples_key(self):
        stats = self._make_stats()
        result = stats.to_dict()
        assert "_samples" not in result

    def test_to_dict_min_duration_zero_when_no_records(self):
        stats = self._make_stats()
        result = stats.to_dict()
        assert result["min_duration_ms"] == 0.0


# ---------------------------------------------------------------------------
# StatsCollector
# ---------------------------------------------------------------------------

class TestStatsCollector:
    @pytest.fixture()
    def collector(self):
        return StatsCollector(max_profiles_per_route=5)

    def test_record_and_get_all_stats(self, collector):
        asyncio.get_event_loop().run_until_complete(
            collector.record("/test", "GET", 100.0, 200)
        )
        stats = asyncio.get_event_loop().run_until_complete(
            collector.get_all_stats()
        )
        assert len(stats) == 1
        assert stats[0]["path"] == "/test"
        assert stats[0]["count"] == 1

    def test_multiple_routes(self, collector):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(collector.record("/a", "GET", 50.0, 200))
        loop.run_until_complete(collector.record("/b", "POST", 200.0, 201))
        stats = loop.run_until_complete(collector.get_all_stats())
        assert len(stats) == 2

    def test_stats_sorted_by_avg_duration_descending(self, collector):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(collector.record("/fast", "GET", 10.0, 200))
        loop.run_until_complete(collector.record("/slow", "GET", 500.0, 200))
        stats = loop.run_until_complete(collector.get_all_stats())
        assert stats[0]["path"] == "/slow"
        assert stats[1]["path"] == "/fast"

    def test_reset_clears_all(self, collector):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(collector.record("/test", "GET", 100.0, 200))
        loop.run_until_complete(collector.reset())
        stats = loop.run_until_complete(collector.get_all_stats())
        assert stats == []

    def test_get_route_history_empty(self, collector):
        loop = asyncio.get_event_loop()
        history = loop.run_until_complete(
            collector.get_route_history("/nonexistent", "GET")
        )
        assert history == []

    def test_get_route_history_returns_records(self, collector):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(collector.record("/test", "GET", 100.0, 200, "profile text"))
        history = loop.run_until_complete(
            collector.get_route_history("/test", "GET")
        )
        assert len(history) == 1
        assert history[0].path == "/test"
        assert history[0].profile_output == "profile text"

    def test_get_route_history_most_recent_first(self, collector):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(collector.record("/test", "GET", 10.0, 200))
        loop.run_until_complete(collector.record("/test", "GET", 20.0, 200))
        loop.run_until_complete(collector.record("/test", "GET", 30.0, 200))
        history = loop.run_until_complete(
            collector.get_route_history("/test", "GET")
        )
        assert history[0].duration_ms == 30.0
        assert history[1].duration_ms == 20.0

    def test_get_route_history_respects_limit(self, collector):
        loop = asyncio.get_event_loop()
        for i in range(5):
            loop.run_until_complete(collector.record("/test", "GET", float(i), 200))
        history = loop.run_until_complete(
            collector.get_route_history("/test", "GET", limit=2)
        )
        assert len(history) == 2

    def test_max_profiles_per_route_enforced(self):
        collector = StatsCollector(max_profiles_per_route=3)
        loop = asyncio.get_event_loop()
        for i in range(10):
            loop.run_until_complete(collector.record("/test", "GET", float(i), 200))
        history = loop.run_until_complete(
            collector.get_route_history("/test", "GET", limit=100)
        )
        assert len(history) == 3

    def test_record_with_profile_output(self, collector):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            collector.record("/test", "GET", 50.0, 200, profile_output="output")
        )
        history = loop.run_until_complete(
            collector.get_route_history("/test", "GET")
        )
        assert history[0].profile_output == "output"

    def test_error_requests_counted(self, collector):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(collector.record("/test", "GET", 10.0, 500))
        loop.run_until_complete(collector.record("/test", "GET", 10.0, 200))
        stats = loop.run_until_complete(collector.get_all_stats())
        assert stats[0]["error_count"] == 1
        assert stats[0]["count"] == 2
