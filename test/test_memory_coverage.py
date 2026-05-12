"""Targeted tests to maximise coverage of the memory profiler modules.

This file complements ``test/test_memory.py``.  It focuses on exception
branches, the shutdown hook, the ``get_shutdown_handler`` accessor and a
few edge cases of the JSON dashboard router that are awkward to express
in the high-level lifecycle tests.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import tracemalloc
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_profiler import (
    MEMRAY_AVAILABLE,
    MemoryProfilerMiddleware,
    MemrayProfiler,
    TracemallocProfiler,
)
from fastapi_profiler.memory import memray_profiler as memray_module
from fastapi_profiler.memory.memray_profiler import (
    IS_WINDOWS,
    MemrayUnavailableError,
)
from fastapi_profiler.memory.tracemalloc_profiler import _format_statistics

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def snapshot_dir():
    path = tempfile.mkdtemp(prefix="fp-mem-cov-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def stopped_tracemalloc():
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    yield
    if tracemalloc.is_tracing():
        tracemalloc.stop()


def make_app(snapshot_dir: str, **kwargs) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        MemoryProfilerMiddleware,
        server_app=app,
        snapshot_dir=snapshot_dir,
        **kwargs,
    )

    @app.get("/test")
    async def _ok():
        return {"ok": True}

    return app


def _run(coro):
    """Run an async coroutine in a freshly-created event loop."""
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# tracemalloc edge cases
# ---------------------------------------------------------------------------


class TestTracemallocFormatStatistics:
    def test_format_statistics_handles_missing_traceback(self):
        """Statistic with empty traceback should yield <unknown>:0."""
        fake = MagicMock()
        fake.traceback = []
        fake.size = 100
        fake.count = 2

        out = _format_statistics([fake], top_n=5)
        assert len(out) == 1
        assert out[0]["file"] == "<unknown>"
        assert out[0]["line"] == 0
        assert out[0]["traceback"] == []

    def test_format_statistics_diff_includes_diff_fields(self):
        frame = MagicMock(filename="/tmp/x.py", lineno=42)
        fake = MagicMock()
        fake.traceback = [frame]
        fake.size = 200
        fake.count = 3
        fake.size_diff = 150
        fake.count_diff = 1

        out = _format_statistics([fake], top_n=5, is_diff=True)
        assert out[0]["size_diff_bytes"] == 150
        assert out[0]["count_diff"] == 1


class TestTracemallocCompareEdgeCases:
    def test_compare_with_invalid_top_rejected(
        self, snapshot_dir, stopped_tracemalloc
    ):
        profiler = TracemallocProfiler(snapshot_dir=snapshot_dir)
        profiler.start()
        try:
            profiler.snapshot()
            profiler.snapshot()
            with pytest.raises(ValueError):
                profiler.compare(top=0)
        finally:
            profiler.stop()


# ---------------------------------------------------------------------------
# memray availability branches (work regardless of memray install state)
# ---------------------------------------------------------------------------


class TestMemrayAvailabilityBranches:
    def test_windows_branch_reported_as_unsupported(
        self, snapshot_dir, monkeypatch
    ):
        """When IS_WINDOWS=True, is_available reports platform_supported=False."""
        monkeypatch.setattr(memray_module, "IS_WINDOWS", True)
        info = MemrayProfiler(output_dir=snapshot_dir).is_available()
        assert info["available"] is False
        assert info["platform_supported"] is False
        assert "Windows" in info["reason"]

    def test_missing_module_branch(self, snapshot_dir, monkeypatch):
        """When MEMRAY_AVAILABLE=False (and not Windows) the install hint is returned."""
        monkeypatch.setattr(memray_module, "IS_WINDOWS", False)
        monkeypatch.setattr(memray_module, "MEMRAY_AVAILABLE", False)
        monkeypatch.setattr(
            memray_module, "_MEMRAY_IMPORT_ERROR", "ModuleNotFoundError: memray"
        )
        info = MemrayProfiler(output_dir=snapshot_dir).is_available()
        assert info["available"] is False
        assert info["platform_supported"] is True
        assert "fastapi_profiler[memray]" in info["reason"]
        assert "import_error" in info

    def test_status_when_unavailable_includes_reason(
        self, snapshot_dir, monkeypatch
    ):
        monkeypatch.setattr(memray_module, "IS_WINDOWS", False)
        monkeypatch.setattr(memray_module, "MEMRAY_AVAILABLE", False)
        status = MemrayProfiler(output_dir=snapshot_dir).status()
        assert status["available"] is False
        assert "reason" in status
        assert status["running"] is False


# ---------------------------------------------------------------------------
# memray exception path: tracker.__enter__ raises
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not MEMRAY_AVAILABLE or IS_WINDOWS,
    reason="memray must be available to exercise the failure-rollback branch",
)
class TestMemrayStartFailureRollback:
    def test_tracker_enter_failure_rolls_back_state(
        self, snapshot_dir, monkeypatch
    ):
        """If Tracker.__enter__ raises, profiler state must remain clean."""

        class _BoomTracker:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                raise RuntimeError("boom")

            def __exit__(self, exc_type, exc, tb):  # pragma: no cover - never reached
                return False

        monkeypatch.setattr(memray_module.memray, "Tracker", _BoomTracker)

        profiler = MemrayProfiler(output_dir=snapshot_dir)
        with pytest.raises(RuntimeError, match="boom"):
            profiler.start()

        # State must be fully reset so a subsequent start() works.
        assert profiler.is_running() is False
        status = profiler.status()
        assert status["running"] is False
        assert "session_id" not in status


# ---------------------------------------------------------------------------
# memray full lifecycle (only when memray is available locally)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not MEMRAY_AVAILABLE or IS_WINDOWS,
    reason="memray is not installed in this environment",
)
class TestMemrayStatusWhileRunning:
    def test_status_running_includes_session_fields(self, snapshot_dir):
        profiler = MemrayProfiler(output_dir=snapshot_dir)
        profiler.start()
        try:
            status = profiler.status()
            assert status["running"] is True
            assert "session_id" in status
            assert "output_path" in status
            assert "started_at" in status
            assert status["running_seconds"] >= 0
        finally:
            profiler.stop()

    def test_stop_returns_hint_with_path(self, snapshot_dir):
        profiler = MemrayProfiler(output_dir=snapshot_dir)
        profiler.start()
        result = profiler.stop()
        assert result["hint"] is not None
        assert "memray flamegraph" in result["hint"]
        assert result["file_size_bytes"] > 0

    def test_explicit_output_path_is_used(self, snapshot_dir):
        explicit = os.path.join(snapshot_dir, "explicit.bin")
        profiler = MemrayProfiler(output_dir=snapshot_dir)
        result = profiler.start(output_path=explicit)
        try:
            assert result["output_path"] == explicit
        finally:
            stop = profiler.stop()
        assert stop["output_path"] == explicit
        assert os.path.isfile(explicit)


# ---------------------------------------------------------------------------
# Dashboard router error branches
# ---------------------------------------------------------------------------


class TestDashboardErrorBranches:
    def test_tracemalloc_compare_invalid_snapshot_a_type(
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
                json={"snapshot_a": 123, "snapshot_b": "ok"},
            )
            assert response.status_code == 400
            assert "snapshot_a" in response.json()["error"]
        finally:
            client.post("/__memory_profiler__/tracemalloc/stop")

    def test_tracemalloc_compare_invalid_snapshot_b_type(
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
                json={"snapshot_a": "ok", "snapshot_b": 456},
            )
            assert response.status_code == 400
            assert "snapshot_b" in response.json()["error"]
        finally:
            client.post("/__memory_profiler__/tracemalloc/stop")

    def test_tracemalloc_compare_invalid_top_returns_400(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        client.post("/__memory_profiler__/tracemalloc/start")
        try:
            response = client.post(
                "/__memory_profiler__/tracemalloc/compare",
                json={"top": "huge"},
            )
            assert response.status_code == 400
        finally:
            client.post("/__memory_profiler__/tracemalloc/stop")

    def test_tracemalloc_compare_zero_top_returns_400(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        client.post("/__memory_profiler__/tracemalloc/start")
        try:
            response = client.post(
                "/__memory_profiler__/tracemalloc/compare",
                json={"top": 0},
            )
            assert response.status_code == 400
        finally:
            client.post("/__memory_profiler__/tracemalloc/stop")

    def test_tracemalloc_snapshot_zero_top_returns_400(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        client.post("/__memory_profiler__/tracemalloc/start")
        try:
            response = client.post(
                "/__memory_profiler__/tracemalloc/snapshot",
                json={"top": 0},
            )
            assert response.status_code == 400
        finally:
            client.post("/__memory_profiler__/tracemalloc/stop")

    def test_tracemalloc_start_non_int_frames_returns_400(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        response = client.post(
            "/__memory_profiler__/tracemalloc/start",
            json={"frames": "many"},
        )
        assert response.status_code == 400

    def test_json_body_must_be_object(self, snapshot_dir, stopped_tracemalloc):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        response = client.post(
            "/__memory_profiler__/tracemalloc/start",
            content=b"[1, 2, 3]",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
        assert "object" in response.json()["error"]

    def test_memray_invalid_output_path_type_returns_400(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        response = client.post(
            "/__memory_profiler__/memray/start",
            json={"output_path": 12345},
        )
        assert response.status_code == 400
        assert "output_path" in response.json()["error"]

    def test_memray_invalid_json_body_returns_400(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        response = client.post(
            "/__memory_profiler__/memray/start",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400


@pytest.mark.skipif(
    not MEMRAY_AVAILABLE or IS_WINDOWS,
    reason="requires memray to exercise the 409 already-running branch",
)
class TestMemrayDoubleStartViaHTTP:
    def test_starting_twice_returns_409(self, snapshot_dir, stopped_tracemalloc):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        first = client.post("/__memory_profiler__/memray/start")
        try:
            assert first.status_code == 200
            second = client.post("/__memory_profiler__/memray/start")
            assert second.status_code == 409
        finally:
            client.post("/__memory_profiler__/memray/stop")


# ---------------------------------------------------------------------------
# MemoryProfilerMiddleware: shutdown hook + accessors
# ---------------------------------------------------------------------------


class TestMiddlewareAccessors:
    def test_property_accessors_return_inner_profilers(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = FastAPI()
        mw = MemoryProfilerMiddleware(
            app,
            server_app=app,
            snapshot_dir=snapshot_dir,
            memory_dashboard_path="/__mem__",
        )
        assert isinstance(mw.tracemalloc_profiler, TracemallocProfiler)
        assert isinstance(mw.memray_profiler, MemrayProfiler)
        assert mw.dashboard_path == "/__mem__"
        # bound methods compare equal even when not identity-equal.
        handler = mw.get_shutdown_handler()
        assert handler is not None
        assert handler == mw._on_shutdown
        assert asyncio.iscoroutinefunction(handler)

    def test_dashboard_path_normalisation(
        self, snapshot_dir, stopped_tracemalloc
    ):
        """Trailing slashes are stripped; empty path falls back to default."""
        app = FastAPI()
        mw = MemoryProfilerMiddleware(
            app,
            server_app=app,
            snapshot_dir=snapshot_dir,
            memory_dashboard_path="/__mem__/",
        )
        assert mw.dashboard_path == "/__mem__"

        app2 = FastAPI()
        mw2 = MemoryProfilerMiddleware(
            app2,
            server_app=app2,
            snapshot_dir=snapshot_dir,
            memory_dashboard_path="/",
        )
        assert mw2.dashboard_path == MemoryProfilerMiddleware.DEFAULT_PATH


class TestMiddlewareShutdownHook:
    def test_shutdown_stops_running_tracemalloc(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = FastAPI()
        mw = MemoryProfilerMiddleware(
            app, server_app=app, snapshot_dir=snapshot_dir
        )
        mw.tracemalloc_profiler.start(frames=5)
        assert tracemalloc.is_tracing() is True

        _run(mw._on_shutdown())
        assert tracemalloc.is_tracing() is False

    def test_shutdown_when_nothing_running_is_noop(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = FastAPI()
        mw = MemoryProfilerMiddleware(
            app, server_app=app, snapshot_dir=snapshot_dir
        )
        # Should not raise even when neither profiler is active.
        _run(mw._on_shutdown())

    def test_shutdown_swallows_tracemalloc_errors(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = FastAPI()
        mw = MemoryProfilerMiddleware(
            app, server_app=app, snapshot_dir=snapshot_dir
        )
        # Force is_running to True but make stop() raise.
        with patch.object(
            mw._tracemalloc, "is_running", return_value=True
        ), patch.object(
            mw._tracemalloc, "stop", side_effect=RuntimeError("boom")
        ):
            # Must not propagate.
            _run(mw._on_shutdown())

    def test_shutdown_swallows_memray_errors(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = FastAPI()
        mw = MemoryProfilerMiddleware(
            app, server_app=app, snapshot_dir=snapshot_dir
        )
        with patch.object(
            mw._memray, "is_running", return_value=True
        ), patch.object(
            mw._memray, "stop", side_effect=RuntimeError("kaboom")
        ):
            _run(mw._on_shutdown())

    @pytest.mark.skipif(
        not MEMRAY_AVAILABLE or IS_WINDOWS,
        reason="memray needed to truly stop a running session",
    )
    def test_shutdown_finalises_running_memray(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = FastAPI()
        mw = MemoryProfilerMiddleware(
            app, server_app=app, snapshot_dir=snapshot_dir
        )
        mw.memray_profiler.start()
        assert mw.memray_profiler.is_running() is True
        _run(mw._on_shutdown())
        assert mw.memray_profiler.is_running() is False


class TestAutostartWithoutServerApp:
    def test_warns_and_still_autostarts(
        self, snapshot_dir, stopped_tracemalloc
    ):
        """server_app=None still allows autostart_tracemalloc to run."""
        import warnings as _warnings

        from starlette.applications import Starlette

        plain_app = Starlette()
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            mw = MemoryProfilerMiddleware(
                plain_app,
                snapshot_dir=snapshot_dir,
                autostart_tracemalloc=True,
            )
        try:
            assert any(
                "MemoryProfilerMiddleware requires server_app" in str(w.message)
                for w in caught
            )
            assert mw.tracemalloc_profiler.is_running() is True
        finally:
            mw.tracemalloc_profiler.stop()


# ---------------------------------------------------------------------------
# tracemalloc HTTP: status while running exposes traced_memory_*
# ---------------------------------------------------------------------------


class TestTracemallocStatusFields:
    def test_status_running_exposes_traced_memory(
        self, snapshot_dir, stopped_tracemalloc
    ):
        app = make_app(snapshot_dir)
        client = TestClient(app)
        client.post("/__memory_profiler__/tracemalloc/start", json={"frames": 4})
        try:
            data = client.get("/__memory_profiler__/tracemalloc/status").json()
            assert data["running"] is True
            assert data["frames"] == 4
            assert data["traced_memory_current_bytes"] >= 0
            assert data["traced_memory_peak_bytes"] >= 0
        finally:
            client.post("/__memory_profiler__/tracemalloc/stop")


# ---------------------------------------------------------------------------
# Patched dashboard error-mapping branches
#
# A few exception → HTTP status mappings in memory_dashboard.py are hard to
# reach in normal lifecycle tests because the underlying profiler does not
# raise from the corresponding code paths.  We patch the profilers to raise
# deterministically so each ``except`` branch is exercised.
# ---------------------------------------------------------------------------


def _make_app_with_profilers(snapshot_dir, tm_profiler, mr_profiler):
    """Build an app whose memory router uses externally-provided profilers."""
    from fastapi_profiler.memory_dashboard import create_memory_router

    app = FastAPI()
    router = create_memory_router(
        tracemalloc_profiler=tm_profiler,
        memray_profiler=mr_profiler,
    )
    app.mount("/__memory_profiler__", app=router)
    return app


class _FakeTracemalloc:
    """Minimal stand-in for TracemallocProfiler with controllable errors."""

    def __init__(self):
        self._snapshots = []

    def status(self):
        return {"running": False}

    def start(self, frames=None):
        # Force the dashboard's ``except ValueError`` branch.
        raise ValueError("frames must be >= 1")

    def stop(self):
        return {"running": False}

    def snapshot(self, top=None):
        # Force the dashboard's ``except ValueError`` branch from snapshot().
        raise ValueError("bogus top")

    def compare(self, top=None, snapshot_a=None, snapshot_b=None):
        # Force the dashboard's ``except ValueError`` branch from compare().
        raise ValueError("snapshots must both be set")

    def list_snapshots(self):
        return []


class TestDashboardExceptionMapping:
    def test_tm_start_value_error_returns_400(
        self, snapshot_dir, stopped_tracemalloc
    ):
        real_mr = MemrayProfiler(output_dir=snapshot_dir)
        app = _make_app_with_profilers(snapshot_dir, _FakeTracemalloc(), real_mr)
        client = TestClient(app)
        response = client.post(
            "/__memory_profiler__/tracemalloc/start", json={"frames": 5}
        )
        assert response.status_code == 400
        assert "frames" in response.json()["error"]

    def test_tm_snapshot_value_error_returns_400(
        self, snapshot_dir, stopped_tracemalloc
    ):
        real_mr = MemrayProfiler(output_dir=snapshot_dir)
        app = _make_app_with_profilers(snapshot_dir, _FakeTracemalloc(), real_mr)
        client = TestClient(app)
        response = client.post(
            "/__memory_profiler__/tracemalloc/snapshot", json={"top": 5}
        )
        assert response.status_code == 400
        assert "bogus" in response.json()["error"]

    def test_tm_compare_value_error_returns_400(
        self, snapshot_dir, stopped_tracemalloc
    ):
        real_mr = MemrayProfiler(output_dir=snapshot_dir)
        app = _make_app_with_profilers(snapshot_dir, _FakeTracemalloc(), real_mr)
        client = TestClient(app)
        response = client.post(
            "/__memory_profiler__/tracemalloc/compare", json={"top": 5}
        )
        assert response.status_code == 400
        assert "must both" in response.json()["error"]

    def test_tm_compare_keyerror_returns_404(
        self, snapshot_dir, stopped_tracemalloc
    ):
        """Two known snapshots + one bogus id triggers KeyError → 404."""
        app = make_app(snapshot_dir)
        client = TestClient(app)
        client.post("/__memory_profiler__/tracemalloc/start")
        try:
            snap1 = client.post(
                "/__memory_profiler__/tracemalloc/snapshot"
            ).json()
            client.post("/__memory_profiler__/tracemalloc/snapshot")
            response = client.post(
                "/__memory_profiler__/tracemalloc/compare",
                json={
                    "snapshot_a": snap1["snapshot_id"],
                    "snapshot_b": "definitely-not-a-real-id",
                },
            )
            assert response.status_code == 404
        finally:
            client.post("/__memory_profiler__/tracemalloc/stop")


# ---------------------------------------------------------------------------
# memray import-failure branch (lines 39-42)
#
# We can't easily un-import memray after pytest started, but we *can* run
# the whole memray_profiler module in a subprocess where memray is hidden
# via PYTHONPATH manipulation.  That executes the import-failure branch
# inside a fresh Python process and reports the result.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    IS_WINDOWS,
    reason="memray is not supported on Windows; import always fails natively",
)
class TestMemrayImportFailureBranch:
    def test_import_failure_sets_flags(self, tmp_path):
        """Spawn a subprocess where ``import memray`` is forced to fail."""
        import subprocess
        import textwrap

        # Create a fake `memray` package that raises on import to trigger the
        # except-branch in fastapi_profiler.memory.memray_profiler.
        fake_pkg_dir = tmp_path / "fake-memray"
        fake_pkg_dir.mkdir()
        (fake_pkg_dir / "memray.py").write_text(
            "raise ImportError('forced failure for coverage')\n"
        )

        # Minimal driver script.  Prepending fake_pkg_dir ensures the bogus
        # `memray` shadows the real one.  Then we re-import the profiler
        # module *fresh* and assert it captured the import failure.
        driver = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(fake_pkg_dir)!r})
            # Make sure neither ``memray`` nor the profiler module is cached.
            for mod in list(sys.modules):
                if mod == 'memray' or mod.startswith(
                    'fastapi_profiler.memory.memray_profiler'
                ):
                    sys.modules.pop(mod, None)

            from fastapi_profiler.memory import memray_profiler as m
            assert m.MEMRAY_AVAILABLE is False, m.MEMRAY_AVAILABLE
            assert m.memray is None
            assert 'forced failure' in (m._MEMRAY_IMPORT_ERROR or '')

            # Also verify is_available + _require_available behave correctly.
            info = m.MemrayProfiler(output_dir='/tmp').is_available()
            assert info['available'] is False
            assert 'fastapi_profiler[memray]' in info['reason']
            print('OK')
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", driver],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# memray stop() branches: started_at=None edge (line 255 area)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not MEMRAY_AVAILABLE or IS_WINDOWS,
    reason="memray needed to manipulate internal state",
)
class TestMemrayStopEdgeCases:
    def test_stop_with_missing_started_at(self, snapshot_dir):
        """Defensive branch: started_at=None → duration_seconds == 0.0."""
        profiler = MemrayProfiler(output_dir=snapshot_dir)
        profiler.start()
        # Pretend the timestamp got lost (defensive code path).
        profiler._started_at = None
        result = profiler.stop()
        assert result["running"] is False
        assert result["duration_seconds"] == 0.0

    def test_stop_with_missing_output_path(self, snapshot_dir):
        """When output path is gone, file_size_bytes falls back to 0."""
        profiler = MemrayProfiler(output_dir=snapshot_dir)
        profiler.start()
        # Force the "no file on disk" branch.
        profiler._current_output = "/nonexistent/path/does-not-exist.bin"
        result = profiler.stop()
        assert result["file_size_bytes"] == 0
        assert "memray flamegraph" in (result["hint"] or "")


# ---------------------------------------------------------------------------
# memray _require_available raise branch (line 255)
# ---------------------------------------------------------------------------


class TestMemrayRequireAvailableRaises:
    def test_require_available_raises_when_unavailable(
        self, snapshot_dir, monkeypatch
    ):
        """Force is_available=False so _require_available raises."""
        monkeypatch.setattr(memray_module, "MEMRAY_AVAILABLE", False)
        monkeypatch.setattr(memray_module, "IS_WINDOWS", False)
        profiler = MemrayProfiler(output_dir=snapshot_dir)
        with pytest.raises(MemrayUnavailableError):
            profiler._require_available()

    def test_require_available_with_empty_reason(
        self, snapshot_dir, monkeypatch
    ):
        """If is_available returns reason=None for the not-available branch,
        _require_available must still raise with a default message."""
        profiler = MemrayProfiler(output_dir=snapshot_dir)

        def fake_is_available():
            return {
                "available": False,
                "platform_supported": True,
                "reason": None,
            }

        monkeypatch.setattr(profiler, "is_available", fake_is_available)
        with pytest.raises(MemrayUnavailableError, match="memray unavailable"):
            profiler._require_available()


# ---------------------------------------------------------------------------
# Dashboard _read_json_body / helper coverage (lines 98-100, 170-171, 192-193, 257)
# ---------------------------------------------------------------------------


class _FakeMemrayUnavailable:
    """Stand-in MemrayProfiler whose start() raises MemrayUnavailableError.

    Used so the dashboard's ``except MemrayUnavailableError`` branch (which
    is not reachable via ``make_app`` when memray is installed) can be hit
    deterministically.
    """

    def status(self):
        return {"available": False, "running": False}

    def is_available(self):
        return {
            "available": False,
            "platform_supported": True,
            "reason": "stubbed unavailable",
        }

    def start(self, output_path=None, **kwargs):
        raise MemrayUnavailableError("stubbed unavailable")

    def stop(self):
        return {"running": False, "message": "not running"}


class _FakeMemrayAlreadyRunning:
    """Stand-in MemrayProfiler whose start() raises RuntimeError (already running)."""

    def status(self):
        return {"available": True, "running": True}

    def is_available(self):
        return {"available": True, "platform_supported": True, "reason": None}

    def start(self, output_path=None, **kwargs):
        raise RuntimeError("memray tracker already running; call stop() first")

    def stop(self):
        return {"running": False}


class TestDashboardMemrayBranches:
    def test_memray_unavailable_returns_503(self, snapshot_dir, stopped_tracemalloc):
        real_tm = TracemallocProfiler(snapshot_dir=snapshot_dir)
        app = _make_app_with_profilers(snapshot_dir, real_tm, _FakeMemrayUnavailable())
        client = TestClient(app)
        response = client.post("/__memory_profiler__/memray/start")
        assert response.status_code == 503
        body = response.json()
        assert "stubbed unavailable" in body["error"]
        assert body["availability"]["available"] is False

    def test_memray_runtime_error_returns_409(
        self, snapshot_dir, stopped_tracemalloc
    ):
        real_tm = TracemallocProfiler(snapshot_dir=snapshot_dir)
        app = _make_app_with_profilers(
            snapshot_dir, real_tm, _FakeMemrayAlreadyRunning()
        )
        client = TestClient(app)
        response = client.post("/__memory_profiler__/memray/start")
        assert response.status_code == 409
        assert "already running" in response.json()["error"]


class TestDashboardJsonBodyHelper:
    """Directly exercise _read_json_body's branches via Starlette's TestClient.

    Each test posts a deliberately-malformed body to a route that calls
    ``_read_json_body``; the resulting 400 / 200 confirms which branch ran.
    """

    def test_empty_body_returns_none_branch(
        self, snapshot_dir, stopped_tracemalloc
    ):
        """Empty POST body → `_read_json_body` returns None → start succeeds."""
        app = make_app(snapshot_dir)
        client = TestClient(app)
        # No body, no Content-Type — exercises the ``if not raw: return None`` branch.
        response = client.post("/__memory_profiler__/tracemalloc/start")
        try:
            assert response.status_code == 200
            assert response.json()["running"] is True
        finally:
            client.post("/__memory_profiler__/tracemalloc/stop")

    def test_non_object_body_returns_400(
        self, snapshot_dir, stopped_tracemalloc
    ):
        """JSON array (not object) → ValueError → 400."""
        app = make_app(snapshot_dir)
        client = TestClient(app)
        response = client.post(
            "/__memory_profiler__/tracemalloc/snapshot",
            content=b'"a-string"',
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
        assert "object" in response.json()["error"]

    def test_invalid_json_on_compare_returns_400(
        self, snapshot_dir, stopped_tracemalloc
    ):
        """Invalid JSON on /tracemalloc/compare → 400."""
        app = make_app(snapshot_dir)
        client = TestClient(app)
        response = client.post(
            "/__memory_profiler__/tracemalloc/compare",
            content=b"<not-json>",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
        assert "invalid JSON" in response.json()["error"]

    def test_invalid_json_on_snapshot_returns_400(
        self, snapshot_dir, stopped_tracemalloc
    ):
        """Invalid JSON on /tracemalloc/snapshot → 400."""
        app = make_app(snapshot_dir)
        client = TestClient(app)
        response = client.post(
            "/__memory_profiler__/tracemalloc/snapshot",
            content=b"<not-json>",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
        assert "invalid JSON" in response.json()["error"]


# ---------------------------------------------------------------------------
# tracemalloc _resolve_pair_locked KeyError branch (lines 234-236)
#
# The dashboard test only triggers KeyError via the route (which strips the
# message); here we exercise it directly to be explicit about the branch.
# ---------------------------------------------------------------------------


class TestTracemallocResolvePairKeyError:
    def test_unknown_a_and_b_ids_raise(self, snapshot_dir, stopped_tracemalloc):
        profiler = TracemallocProfiler(snapshot_dir=snapshot_dir)
        profiler.start()
        try:
            profiler.snapshot()
            profiler.snapshot()
            with pytest.raises(KeyError):
                profiler.compare(snapshot_a="missing-a", snapshot_b="missing-b")
        finally:
            profiler.stop()

    def test_only_one_id_unknown_still_raises(
        self, snapshot_dir, stopped_tracemalloc
    ):
        profiler = TracemallocProfiler(snapshot_dir=snapshot_dir)
        profiler.start()
        try:
            snap1 = profiler.snapshot()
            profiler.snapshot()
            with pytest.raises(KeyError):
                profiler.compare(
                    snapshot_a=snap1["snapshot_id"],
                    snapshot_b="missing-b",
                )
        finally:
            profiler.stop()
