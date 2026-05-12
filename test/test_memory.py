"""Unit tests for the memory profiler sub-package and middleware."""

from __future__ import annotations

import os
import shutil
import tempfile
import tracemalloc

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_profiler import (
    MEMRAY_AVAILABLE,
    MemoryProfilerMiddleware,
    MemrayProfiler,
    TracemallocProfiler,
)
from fastapi_profiler.memory.memray_profiler import (
    IS_WINDOWS,
    MemrayUnavailableError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def snapshot_dir():
    """Provide a clean temporary directory for snapshot files."""
    path = tempfile.mkdtemp(prefix="fp-mem-test-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def stopped_tracemalloc():
    """Ensure tracemalloc is stopped before and after the test."""
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    yield
    if tracemalloc.is_tracing():
        tracemalloc.stop()


def make_app(snapshot_dir: str, **kwargs) -> tuple:
    """Create a FastAPI app with MemoryProfilerMiddleware mounted."""
    app = FastAPI()
    app.add_middleware(
        MemoryProfilerMiddleware,
        server_app=app,
        snapshot_dir=snapshot_dir,
        **kwargs,
    )

    @app.get("/test")
    async def normal_request():
        return {"ok": True}

    return app


# ---------------------------------------------------------------------------
# TracemallocProfiler unit tests
# ---------------------------------------------------------------------------


class TestTracemallocProfilerLifecycle:
    def test_start_stop_status(self, snapshot_dir, stopped_tracemalloc):
        profiler = TracemallocProfiler(snapshot_dir=snapshot_dir)
        assert profiler.is_running() is False

        result = profiler.start(frames=10)
        assert result["running"] is True
        assert result["frames"] == 10
        assert profiler.is_running() is True

        status = profiler.status()
        assert status["running"] is True
        assert status["frames"] == 10
        assert status["snapshot_count"] == 0

        stop_result = profiler.stop()
        assert stop_result["running"] is False
        assert profiler.is_running() is False

    def test_double_start_is_noop(self, snapshot_dir, stopped_tracemalloc):
        profiler = TracemallocProfiler(snapshot_dir=snapshot_dir)
        profiler.start(frames=5)
        result = profiler.start(frames=99)  # Should not change frames.
        assert result["message"] == "already running"
        assert profiler.status()["frames"] == 5
        profiler.stop()

    def test_stop_when_not_running(self, snapshot_dir, stopped_tracemalloc):
        profiler = TracemallocProfiler(snapshot_dir=snapshot_dir)
        result = profiler.stop()
        assert result["running"] is False
        assert result["message"] == "not running"

    def test_invalid_frames_rejected(self, snapshot_dir, stopped_tracemalloc):
        profiler = TracemallocProfiler(snapshot_dir=snapshot_dir)
        with pytest.raises(ValueError):
            profiler.start(frames=0)


class TestTracemallocSnapshot:
    def test_snapshot_requires_running(self, snapshot_dir, stopped_tracemalloc):
        profiler = TracemallocProfiler(snapshot_dir=snapshot_dir)
        with pytest.raises(RuntimeError):
            profiler.snapshot()

    def test_snapshot_writes_file(self, snapshot_dir, stopped_tracemalloc):
        profiler = TracemallocProfiler(snapshot_dir=snapshot_dir)
        profiler.start(frames=5)
        try:
            # Allocate something so the snapshot has content.
            payload = ["x" * 128 for _ in range(500)]
            assert payload  # keep ref alive

            result = profiler.snapshot(top=5)
            assert "snapshot_id" in result
            assert os.path.isfile(result["file_path"])
            assert result["file_path"].endswith(".snap")
            assert result["traced_memory_current_bytes"] >= 0
            assert isinstance(result["top"], list)
            assert len(result["top"]) <= 5
        finally:
            profiler.stop()

    def test_snapshot_invalid_top_rejected(self, snapshot_dir, stopped_tracemalloc):
        profiler = TracemallocProfiler(snapshot_dir=snapshot_dir)
        profiler.start()
        try:
            with pytest.raises(ValueError):
                profiler.snapshot(top=0)
        finally:
            profiler.stop()

    def test_list_snapshots_tracks_history(self, snapshot_dir, stopped_tracemalloc):
        profiler = TracemallocProfiler(snapshot_dir=snapshot_dir)
        profiler.start()
        try:
            profiler.snapshot()
            profiler.snapshot()
            entries = profiler.list_snapshots()
            assert len(entries) == 2
            assert all("snapshot_id" in e and "file_path" in e for e in entries)
        finally:
            profiler.stop()

    def test_stop_clears_snapshot_state(self, snapshot_dir, stopped_tracemalloc):
        profiler = TracemallocProfiler(snapshot_dir=snapshot_dir)
        profiler.start()
        profiler.snapshot()
        profiler.stop()
        assert profiler.list_snapshots() == []


class TestTracemallocCompare:
    def test_compare_requires_two_snapshots(self, snapshot_dir, stopped_tracemalloc):
        profiler = TracemallocProfiler(snapshot_dir=snapshot_dir)
        profiler.start()
        try:
            with pytest.raises(RuntimeError):
                profiler.compare()
            profiler.snapshot()
            with pytest.raises(RuntimeError):
                profiler.compare()
        finally:
            profiler.stop()

    def test_compare_returns_top_growers(self, snapshot_dir, stopped_tracemalloc):
        profiler = TracemallocProfiler(snapshot_dir=snapshot_dir)
        profiler.start()
        try:
            profiler.snapshot()
            # Allocate something between snapshots to create a diff.
            growth = ["y" * 256 for _ in range(2000)]
            assert growth
            profiler.snapshot()

            result = profiler.compare(top=10)
            assert "snapshot_a" in result
            assert "snapshot_b" in result
            assert isinstance(result["top"], list)
            for entry in result["top"]:
                assert "size_diff_bytes" in entry
                assert "count_diff" in entry
        finally:
            profiler.stop()

    def test_compare_unknown_id_raises(self, snapshot_dir, stopped_tracemalloc):
        profiler = TracemallocProfiler(snapshot_dir=snapshot_dir)
        profiler.start()
        try:
            profiler.snapshot()
            profiler.snapshot()
            with pytest.raises(KeyError):
                profiler.compare(snapshot_a="bogus", snapshot_b="also-bogus")
        finally:
            profiler.stop()

    def test_compare_partial_ids_rejected(self, snapshot_dir, stopped_tracemalloc):
        profiler = TracemallocProfiler(snapshot_dir=snapshot_dir)
        profiler.start()
        try:
            profiler.snapshot()
            profiler.snapshot()
            with pytest.raises(ValueError):
                profiler.compare(snapshot_a="only-one")
        finally:
            profiler.stop()


# ---------------------------------------------------------------------------
# MemrayProfiler unit tests (degrade gracefully when memray is unavailable)
# ---------------------------------------------------------------------------


class TestMemrayAvailability:
    def test_is_available_shape(self, snapshot_dir):
        profiler = MemrayProfiler(output_dir=snapshot_dir)
        info = profiler.is_available()
        assert "available" in info
        assert "platform_supported" in info
        assert "reason" in info

    def test_status_includes_availability(self, snapshot_dir):
        profiler = MemrayProfiler(output_dir=snapshot_dir)
        status = profiler.status()
        assert "available" in status
        assert status["running"] is False

    def test_start_when_unavailable_raises(self, snapshot_dir):
        if MEMRAY_AVAILABLE and not IS_WINDOWS:
            pytest.skip("memray is available on this platform")
        profiler = MemrayProfiler(output_dir=snapshot_dir)
        with pytest.raises(MemrayUnavailableError):
            profiler.start()


@pytest.mark.skipif(
    not MEMRAY_AVAILABLE or IS_WINDOWS,
    reason="memray is not available on this platform",
)
class TestMemrayLifecycle:
    def test_start_stop_creates_bin(self, snapshot_dir):
        profiler = MemrayProfiler(output_dir=snapshot_dir)
        result = profiler.start()
        try:
            assert result["running"] is True
            assert result["output_path"].endswith(".bin")
            assert profiler.is_running() is True
        finally:
            stop_result = profiler.stop()
        assert stop_result["running"] is False
        assert os.path.isfile(stop_result["output_path"])

    def test_double_start_rejected(self, snapshot_dir):
        profiler = MemrayProfiler(output_dir=snapshot_dir)
        profiler.start()
        try:
            with pytest.raises(RuntimeError):
                profiler.start()
        finally:
            profiler.stop()

    def test_stop_when_not_running(self, snapshot_dir):
        profiler = MemrayProfiler(output_dir=snapshot_dir)
        result = profiler.stop()
        assert result["running"] is False
        assert result["message"] == "not running"


# ---------------------------------------------------------------------------
# HTTP control plane tests (via MemoryProfilerMiddleware)
# ---------------------------------------------------------------------------


class TestMiddlewareMounting:
    def test_index_returns_html(self, snapshot_dir, stopped_tracemalloc):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        response = client.get("/__memory_profiler__/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Memory Profiler" in response.text

    def test_combined_status(self, snapshot_dir, stopped_tracemalloc):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        data = client.get("/__memory_profiler__/status").json()
        assert "tracemalloc" in data
        assert "memray" in data
        assert data["tracemalloc"]["running"] is False

    def test_normal_request_passthrough(self, snapshot_dir, stopped_tracemalloc):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        response = client.get("/test")
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    def test_custom_dashboard_path(self, snapshot_dir, stopped_tracemalloc):
        app = make_app(snapshot_dir, memory_dashboard_path="/__mem__")
        client = TestClient(app)
        assert client.get("/__mem__/").status_code == 200
        assert client.get("/__memory_profiler__/").status_code == 404

    def test_warns_without_server_app(self, snapshot_dir, stopped_tracemalloc):
        import warnings as _warnings

        from starlette.applications import Starlette

        plain_app = Starlette()
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            MemoryProfilerMiddleware(plain_app, snapshot_dir=snapshot_dir)
        assert any(
            "MemoryProfilerMiddleware requires server_app" in str(w.message)
            for w in caught
        )


class TestTracemallocHTTP:
    def test_full_lifecycle(self, snapshot_dir, stopped_tracemalloc):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        base = "/__memory_profiler__/tracemalloc"

        # Initially stopped.
        assert client.get(f"{base}/status").json()["running"] is False

        # Start.
        start = client.post(f"{base}/start", json={"frames": 8}).json()
        assert start["running"] is True
        assert start["frames"] == 8

        # Snapshot twice with allocations in between.
        snap1 = client.post(f"{base}/snapshot", json={"top": 5}).json()
        assert "snapshot_id" in snap1
        _ = ["a" * 64 for _ in range(500)]
        snap2 = client.post(f"{base}/snapshot").json()
        assert snap1["snapshot_id"] != snap2["snapshot_id"]

        # Compare.
        compare = client.post(f"{base}/compare", json={"top": 5}).json()
        assert "top" in compare
        assert isinstance(compare["top"], list)

        # List.
        listing = client.get(f"{base}/snapshots").json()
        assert len(listing["snapshots"]) == 2

        # Stop.
        stop = client.post(f"{base}/stop").json()
        assert stop["running"] is False

    def test_snapshot_before_start_returns_409(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        response = client.post("/__memory_profiler__/tracemalloc/snapshot")
        assert response.status_code == 409
        assert "error" in response.json()

    def test_compare_without_snapshots_returns_409(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        client.post("/__memory_profiler__/tracemalloc/start")
        try:
            response = client.post("/__memory_profiler__/tracemalloc/compare")
            assert response.status_code == 409
        finally:
            client.post("/__memory_profiler__/tracemalloc/stop")

    def test_invalid_frames_returns_400(self, snapshot_dir, stopped_tracemalloc):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        response = client.post(
            "/__memory_profiler__/tracemalloc/start",
            json={"frames": 0},
        )
        assert response.status_code == 400

    def test_invalid_top_returns_400(self, snapshot_dir, stopped_tracemalloc):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        client.post("/__memory_profiler__/tracemalloc/start")
        try:
            response = client.post(
                "/__memory_profiler__/tracemalloc/snapshot",
                json={"top": "huge"},
            )
            assert response.status_code == 400
        finally:
            client.post("/__memory_profiler__/tracemalloc/stop")

    def test_invalid_json_body_returns_400(self, snapshot_dir, stopped_tracemalloc):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        response = client.post(
            "/__memory_profiler__/tracemalloc/start",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400

    def test_compare_unknown_snapshot_returns_404(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        client.post("/__memory_profiler__/tracemalloc/start")
        try:
            client.post("/__memory_profiler__/tracemalloc/snapshot")
            client.post("/__memory_profiler__/tracemalloc/snapshot")
            response = client.post(
                "/__memory_profiler__/tracemalloc/compare",
                json={"snapshot_a": "nope", "snapshot_b": "also-nope"},
            )
            assert response.status_code == 404
        finally:
            client.post("/__memory_profiler__/tracemalloc/stop")


class TestMemrayHTTP:
    def test_status_endpoint(self, snapshot_dir, stopped_tracemalloc):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        data = client.get("/__memory_profiler__/memray/status").json()
        assert "available" in data
        assert "platform_supported" in data

    def test_start_when_unavailable_returns_503(
        self, snapshot_dir, stopped_tracemalloc
    ):
        if MEMRAY_AVAILABLE and not IS_WINDOWS:
            pytest.skip("memray is available; this test is for the unavailable path")
        app = make_app(snapshot_dir)
        client = TestClient(app)
        response = client.post("/__memory_profiler__/memray/start")
        assert response.status_code == 503
        body = response.json()
        assert "error" in body
        assert "availability" in body

    @pytest.mark.skipif(
        not MEMRAY_AVAILABLE or IS_WINDOWS,
        reason="memray is not available on this platform",
    )
    def test_full_lifecycle(self, snapshot_dir, stopped_tracemalloc):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        start = client.post(
            "/__memory_profiler__/memray/start",
            json={"native": False, "follow_fork": False},
        )
        assert start.status_code == 200
        try:
            client.get("/test")  # generate some allocations
        finally:
            stop = client.post("/__memory_profiler__/memray/stop").json()
        assert stop["running"] is False
        assert os.path.isfile(stop["output_path"])

    def test_invalid_bool_field_returns_400(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        response = client.post(
            "/__memory_profiler__/memray/start",
            json={"native": "yes"},
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# autostart_tracemalloc behaviour
# ---------------------------------------------------------------------------


class TestAutostart:
    def test_autostart_starts_tracemalloc(self, snapshot_dir, stopped_tracemalloc):
        app = FastAPI()
        app.add_middleware(
            MemoryProfilerMiddleware,
            server_app=app,
            snapshot_dir=snapshot_dir,
            autostart_tracemalloc=True,
        )
        # add_middleware defers wrapping until first request; force it.
        client = TestClient(app)
        client.get("/__memory_profiler__/status")
        try:
            assert tracemalloc.is_tracing() is True
        finally:
            if tracemalloc.is_tracing():
                tracemalloc.stop()
