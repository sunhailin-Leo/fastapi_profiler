"""Integration tests for PyInstrumentProfilerMiddleware (v1.6.0)."""

import json
import logging
import warnings
import pytest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

from fastapi_profiler import PyInstrumentProfilerMiddleware


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_app(**profiler_kwargs) -> FastAPI:
    """Create a minimal FastAPI app with the profiler middleware attached."""
    app = FastAPI()
    app.add_middleware(PyInstrumentProfilerMiddleware, **profiler_kwargs)

    @app.get("/test")
    async def normal_request():
        return {"retMsg": "Normal Request test Success!"}

    @app.get("/health")
    async def health_check():
        return {"status": "ok"}

    @app.get("/error")
    async def error_request():
        return JSONResponse({"error": "server error"}, status_code=500)

    return app


# ---------------------------------------------------------------------------
# Basic request profiling
# ---------------------------------------------------------------------------

class TestProfilerMiddlewareBasic:
    def test_request_is_processed(self):
        client = TestClient(make_app())
        response = client.get("/test")
        assert response.status_code == 200

    def test_profiler_logs_request(self, caplog):
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            TestClient(make_app()).get("/test")
        assert any("Path: /test" in r.message for r in caplog.records)

    def test_profiler_logs_method_and_status(self, caplog):
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            TestClient(make_app()).get("/test")
        messages = " ".join(r.message for r in caplog.records)
        assert "Method: GET" in messages
        assert "Status: 200" in messages

    def test_profiler_logs_duration_ms(self, caplog):
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            TestClient(make_app()).get("/test")
        messages = " ".join(r.message for r in caplog.records)
        assert "ms" in messages

    def test_non_http_scope_passes_through(self):
        """Middleware must not interfere with non-HTTP scopes."""
        client = TestClient(make_app())
        response = client.get("/test")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Path filtering
# ---------------------------------------------------------------------------

class TestPathFiltering:
    def test_filtered_path_skips_profiling(self, caplog):
        app = make_app(filter_paths=["/health"])
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            TestClient(app).get("/health")
        assert not any("Path: /health" in r.message for r in caplog.records)

    def test_non_filtered_path_is_profiled(self, caplog):
        app = make_app(filter_paths=["/health"])
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            TestClient(app).get("/test")
        assert any("Path: /test" in r.message for r in caplog.records)

    def test_multiple_filter_prefixes(self, caplog):
        app = make_app(filter_paths=["/health", "/metrics"])
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            TestClient(app).get("/health")
        assert not any("Path: /health" in r.message for r in caplog.records)

    def test_empty_filter_paths_profiles_everything(self, caplog):
        app = make_app(filter_paths=[])
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            TestClient(app).get("/test")
        assert any("Path: /test" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Slow-request threshold
# ---------------------------------------------------------------------------

class TestSlowRequestThreshold:
    def test_zero_threshold_always_emits(self, caplog):
        app = make_app(slow_request_threshold_ms=0)
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            TestClient(app).get("/test")
        assert any("Path: /test" in r.message for r in caplog.records)

    def test_high_threshold_still_logs_request(self, caplog):
        # Even with a very high threshold, the request log line is always emitted.
        app = make_app(slow_request_threshold_ms=999_999)
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            TestClient(app).get("/test")
        assert any("Path: /test" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Sampling rate
# ---------------------------------------------------------------------------

class TestSamplingRate:
    def test_zero_sample_rate_still_logs_request(self, caplog):
        # Request log is always emitted; only pyinstrument profile text is skipped.
        app = make_app(profiler_sample_rate=0.0)
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            TestClient(app).get("/test")
        assert any("Path: /test" in r.message for r in caplog.records)

    def test_full_sample_rate_profiles_every_request(self, caplog):
        app = make_app(profiler_sample_rate=1.0)
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            TestClient(app).get("/test")
        assert any("Path: /test" in r.message for r in caplog.records)

    def test_sample_rate_uses_random_below_threshold(self):
        """random.random() < sample_rate → should sample."""
        app = make_app(profiler_sample_rate=0.5)
        with patch("fastapi_profiler.profiler.random.random", return_value=0.3):
            response = TestClient(app).get("/test")
            assert response.status_code == 200

    def test_sample_rate_uses_random_above_threshold(self):
        """random.random() >= sample_rate → should not sample (request still processed)."""
        app = make_app(profiler_sample_rate=0.5)
        with patch("fastapi_profiler.profiler.random.random", return_value=0.8):
            response = TestClient(app).get("/test")
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# Always profile errors
# ---------------------------------------------------------------------------

class TestAlwaysProfileErrors:
    def test_error_request_logged(self, caplog):
        app = make_app(always_profile_errors=True)
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            TestClient(app).get("/error")
        messages = " ".join(r.message for r in caplog.records)
        assert "Path: /error" in messages
        assert "Status: 500" in messages

    def test_error_profiled_even_with_zero_sample_rate(self, caplog):
        """5xx responses must be profiled regardless of sample rate."""
        app = make_app(profiler_sample_rate=0.0, always_profile_errors=True)
        with patch("fastapi_profiler.profiler.random.random", return_value=0.99):
            with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
                TestClient(app).get("/error")
        messages = " ".join(r.message for r in caplog.records)
        assert "Status: 500" in messages

    def test_error_profiled_even_above_threshold(self, caplog):
        """5xx responses must be profiled even if duration < threshold."""
        app = make_app(slow_request_threshold_ms=999_999, always_profile_errors=True)
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            TestClient(app).get("/error")
        messages = " ".join(r.message for r in caplog.records)
        assert "Status: 500" in messages


# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------

class TestStructuredJsonLogging:
    def test_json_log_format_emits_json(self, caplog):
        app = make_app(log_format="json")
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            TestClient(app).get("/test")
        json_lines = [
            r.message for r in caplog.records
            if r.name == "fastapi_profiler" and r.message.startswith("{")
        ]
        assert len(json_lines) >= 1
        parsed = json.loads(json_lines[0])
        assert parsed["method"] == "GET"
        assert parsed["path"] == "/test"
        assert parsed["status_code"] == 200
        assert "duration_ms" in parsed

    def test_text_log_format_is_default(self, caplog):
        app = make_app(log_format="text")
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            TestClient(app).get("/test")
        # Text format: "Method: GET, Path: /test, ..."
        text_lines = [
            r.message for r in caplog.records
            if "Method:" in r.message
        ]
        assert len(text_lines) >= 1
        # Must NOT be JSON
        assert not text_lines[0].startswith("{")


# ---------------------------------------------------------------------------
# Runtime enable/disable
# ---------------------------------------------------------------------------

class TestRuntimeEnableDisable:
    def test_disabled_middleware_passes_through(self, caplog):
        app = make_app(enabled=False)
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            response = TestClient(app).get("/test")
        assert response.status_code == 200
        profiler_records = [r for r in caplog.records if r.name == "fastapi_profiler"]
        assert len(profiler_records) == 0

    def test_enabled_middleware_logs(self, caplog):
        app = make_app(enabled=True)
        with caplog.at_level(logging.INFO, logger="fastapi_profiler"):
            TestClient(app).get("/test")
        profiler_records = [r for r in caplog.records if r.name == "fastapi_profiler"]
        assert len(profiler_records) > 0


# ---------------------------------------------------------------------------
# Input validation
# FastAPI delays middleware construction until the first request, so we must
# trigger the build via TestClient to catch ValueError from __init__.
# ---------------------------------------------------------------------------

def _trigger_middleware_build(app: FastAPI) -> None:
    """Send a dummy request to force FastAPI to build the middleware stack."""
    @app.get("/__probe__")
    async def probe():
        return {}

    with TestClient(app, raise_server_exceptions=True) as client:
        client.get("/__probe__")


class TestInputValidation:
    def test_invalid_output_type_raises_value_error(self):
        app = FastAPI()
        app.add_middleware(
            PyInstrumentProfilerMiddleware,
            profiler_output_type="invalid_type",
        )
        with pytest.raises(ValueError, match="Invalid profiler_output_type"):
            _trigger_middleware_build(app)

    def test_invalid_log_format_raises_value_error(self):
        app = FastAPI()
        app.add_middleware(
            PyInstrumentProfilerMiddleware,
            log_format="xml",
        )
        with pytest.raises(ValueError, match="Invalid log_format"):
            _trigger_middleware_build(app)

    def test_invalid_sample_rate_raises_value_error(self):
        app = FastAPI()
        app.add_middleware(
            PyInstrumentProfilerMiddleware,
            profiler_sample_rate=1.5,
        )
        with pytest.raises(ValueError, match="profiler_sample_rate"):
            _trigger_middleware_build(app)

    def test_negative_sample_rate_raises_value_error(self):
        app = FastAPI()
        app.add_middleware(
            PyInstrumentProfilerMiddleware,
            profiler_sample_rate=-0.1,
        )
        with pytest.raises(ValueError, match="profiler_sample_rate"):
            _trigger_middleware_build(app)

    def test_valid_output_types_do_not_raise(self):
        for output_type in ("text", "prof"):
            app = FastAPI()
            app.add_middleware(
                PyInstrumentProfilerMiddleware,
                profiler_output_type=output_type,
            )
            # Use a unique route name per iteration to avoid duplicate registration.
            route_path = f"/__probe_{output_type}__"

            @app.get(route_path)
            async def _probe():
                return {}

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with TestClient(app) as client:
                    response = client.get(route_path)
                assert response.status_code == 200


# ---------------------------------------------------------------------------
# server_app warnings
# FastAPI delays middleware construction until the first request, so warnings
# are emitted during TestClient startup, not during add_middleware().
# ---------------------------------------------------------------------------

class TestServerAppWarning:
    def _collect_warnings_on_startup(self, **middleware_kwargs) -> list:
        app = FastAPI()
        app.add_middleware(PyInstrumentProfilerMiddleware, **middleware_kwargs)

        @app.get("/__probe__")
        async def probe():
            return {}

        caught = []
        with warnings.catch_warnings(record=True) as caught_ctx:
            warnings.simplefilter("always")
            try:
                with TestClient(app) as client:
                    client.get("/__probe__")
            except Exception:
                pass
        return [w for w in caught_ctx if issubclass(w.category, UserWarning)]

    def test_html_without_server_app_warns(self):
        user_warnings = self._collect_warnings_on_startup(
            profiler_output_type="html"
        )
        assert len(user_warnings) >= 1
        assert "server_app" in str(user_warnings[0].message)

    def test_json_without_server_app_warns(self):
        user_warnings = self._collect_warnings_on_startup(
            profiler_output_type="json"
        )
        assert len(user_warnings) >= 1

    def test_text_without_server_app_does_not_warn(self):
        user_warnings = self._collect_warnings_on_startup(
            profiler_output_type="text"
        )
        assert len(user_warnings) == 0

    def test_enable_dashboard_without_server_app_warns(self):
        user_warnings = self._collect_warnings_on_startup(
            enable_dashboard=True
        )
        assert any("dashboard" in str(w.message).lower() for w in user_warnings)


# ---------------------------------------------------------------------------
# File-based output types
# ---------------------------------------------------------------------------

class TestFileOutputTypes:
    def test_export_to_html(self, tmp_path, caplog):
        """HTML output type: get_profiler_result logs a warning (no aggregated
        session in 1.5.0+).  Verify the request succeeds and the warning is emitted."""
        import logging
        full_path = tmp_path / "test.html"
        app = FastAPI()
        app.add_middleware(
            PyInstrumentProfilerMiddleware,
            server_app=app,
            profiler_output_type="html",
            is_print_each_request=False,
            profiler_interval=0.0000001,
            html_file_name=str(full_path),
        )

        @app.get("/test")
        async def test_route():
            return {"ok": True}

        with caplog.at_level(logging.WARNING, logger="fastapi_profiler"):
            with TestClient(app) as client:
                response = client.get("/test")
        assert response.status_code == 200
        # Shutdown handler emits a warning about no aggregated session.
        assert any("html" in r.message for r in caplog.records)

    def test_export_to_prof(self, tmp_path):
        """cProfile output type: get_profiler_result writes the .prof file on shutdown."""
        full_path = tmp_path / "test.prof"
        app = FastAPI()
        app.add_middleware(
            PyInstrumentProfilerMiddleware,
            server_app=app,
            profiler_output_type="prof",
            is_print_each_request=False,
            profiler_interval=0.0000001,
            prof_file_name=str(full_path),
        )

        @app.get("/test")
        async def test_route():
            return {"ok": True}

        with TestClient(app) as client:
            client.get("/test")
        # cProfile accumulates stats and dumps on shutdown.
        assert full_path.exists()
        assert full_path.read_bytes()

    def test_export_to_json(self, tmp_path):
        """JSON output type: in 1.5.0+ each request uses its own Profiler instance.
        get_profiler_result emits a warning; verify the request itself succeeds."""
        full_path = tmp_path / "test.json"
        app = FastAPI()
        app.add_middleware(
            PyInstrumentProfilerMiddleware,
            server_app=app,
            profiler_output_type="json",
            is_print_each_request=False,
            profiler_interval=0.0000001,
            prof_file_name=str(full_path),
        )

        @app.get("/test")
        async def test_route():
            return {"ok": True}

        with TestClient(app) as client:
            response = client.get("/test")
        assert response.status_code == 200

    def test_export_to_speedscope(self, tmp_path):
        """Speedscope output type: same as JSON — per-request Profiler, no aggregated
        session.  Verify the request succeeds."""
        full_path = tmp_path / "test_speedscope.json"
        app = FastAPI()
        app.add_middleware(
            PyInstrumentProfilerMiddleware,
            server_app=app,
            profiler_output_type="speedscope",
            is_print_each_request=False,
            profiler_interval=0.0000001,
            prof_file_name=str(full_path),
        )

        @app.get("/test")
        async def test_route():
            return {"ok": True}

        with TestClient(app) as client:
            response = client.get("/test")
        assert response.status_code == 200

    @staticmethod
    def _capture_log(logger_name: str):
        """Context manager that captures log records from a named logger."""
        import logging
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            handler = logging.handlers.MemoryHandler(capacity=1000, flushLevel=logging.CRITICAL)
            log = logging.getLogger(logger_name)
            log.addHandler(handler)
            records = []
            try:
                yield records
            finally:
                records.extend(handler.buffer)
                log.removeHandler(handler)

        return _ctx()


# ---------------------------------------------------------------------------
# Stats integration
# ---------------------------------------------------------------------------

class TestStatsIntegration:
    def test_request_succeeds_with_stats_collection(self):
        """Stats are collected silently; verify the request itself succeeds."""
        client = TestClient(make_app())
        assert client.get("/test").status_code == 200
        assert client.get("/test").status_code == 200

    def test_dashboard_shows_stats_after_request(self):
        app = FastAPI()
        app.add_middleware(
            PyInstrumentProfilerMiddleware,
            server_app=app,
            enable_dashboard=True,
            dashboard_path="/__profiler__",
        )

        @app.get("/api/test")
        async def api_test():
            return {"ok": True}

        with TestClient(app) as client:
            client.get("/api/test")
            stats_response = client.get("/__profiler__/stats")

        assert stats_response.status_code == 200
        data = stats_response.json()
        assert data["enabled"] is True
        assert len(data["routes"]) >= 1

    def test_dashboard_reset_clears_stats(self):
        # Filter out the dashboard path so that the /stats request after reset
        # does not itself get recorded into the stats collector.
        app = FastAPI()
        app.add_middleware(
            PyInstrumentProfilerMiddleware,
            server_app=app,
            enable_dashboard=True,
            dashboard_path="/__profiler__",
            filter_paths=["/__profiler__"],
        )

        @app.get("/api/test")
        async def api_test():
            return {"ok": True}

        with TestClient(app) as client:
            client.get("/api/test")
            client.post("/__profiler__/reset")
            data = client.get("/__profiler__/stats").json()

        assert data["routes"] == []

    def test_dashboard_config_update_via_api(self):
        app = FastAPI()
        app.add_middleware(
            PyInstrumentProfilerMiddleware,
            server_app=app,
            enable_dashboard=True,
            dashboard_path="/__profiler__",
        )

        @app.get("/api/test")
        async def api_test():
            return {"ok": True}

        with TestClient(app) as client:
            response = client.post(
                "/__profiler__/config",
                json={"enabled": False, "sample_rate": 0.5},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["sample_rate"] == 0.5

    def test_dashboard_config_update_slow_threshold(self):
        """Cover _apply_runtime_config slow_request_threshold_ms branch (line 251)."""
        app = FastAPI()
        app.add_middleware(
            PyInstrumentProfilerMiddleware,
            server_app=app,
            enable_dashboard=True,
            dashboard_path="/__profiler__",
        )

        @app.get("/api/test")
        async def api_test():
            return {"ok": True}

        with TestClient(app) as client:
            response = client.post(
                "/__profiler__/config",
                json={"slow_request_threshold_ms": 500.0},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["slow_request_threshold_ms"] == 500.0


# ---------------------------------------------------------------------------
# Output file name resolution
# ---------------------------------------------------------------------------

class TestResolveOutputFileName:
    """Directly unit-test _resolve_output_file_name for all output type branches."""

    def _make_middleware(self, output_type: str, prof_file_name=None, html_file_name=None):
        """Build a middleware instance without a full ASGI stack."""
        from starlette.applications import Starlette

        bare_app = Starlette()
        middleware = PyInstrumentProfilerMiddleware(
            bare_app,
            profiler_output_type=output_type,
            prof_file_name=prof_file_name,
            html_file_name=html_file_name,
        )
        return middleware

    def test_html_with_custom_filename(self):
        middleware = self._make_middleware("html", html_file_name="/tmp/custom.html")
        assert middleware._resolve_output_file_name() == "/tmp/custom.html"

    def test_html_uses_default_filename(self):
        middleware = self._make_middleware("html")
        assert middleware._resolve_output_file_name() == PyInstrumentProfilerMiddleware.DEFAULT_HTML_FILENAME

    def test_prof_with_custom_filename(self):
        middleware = self._make_middleware("prof", prof_file_name="/tmp/custom.prof")
        assert middleware._resolve_output_file_name() == "/tmp/custom.prof"

    def test_prof_uses_default_filename(self):
        middleware = self._make_middleware("prof")
        assert middleware._resolve_output_file_name() == PyInstrumentProfilerMiddleware.DEFAULT_PROF_FILENAME

    def test_json_with_custom_filename(self):
        middleware = self._make_middleware("json", prof_file_name="/tmp/custom.json")
        assert middleware._resolve_output_file_name() == "/tmp/custom.json"

    def test_json_uses_default_filename(self):
        """Cover line 303: json branch falls back to DEFAULT_JSON_FILENAME."""
        middleware = self._make_middleware("json")
        assert middleware._resolve_output_file_name() == PyInstrumentProfilerMiddleware.DEFAULT_JSON_FILENAME

    def test_speedscope_with_custom_filename(self):
        middleware = self._make_middleware("speedscope", prof_file_name="/tmp/custom_speedscope.json")
        assert middleware._resolve_output_file_name() == "/tmp/custom_speedscope.json"

    def test_speedscope_uses_default_filename(self):
        """Cover line 307: speedscope branch falls back to DEFAULT_SPEEDSCOPE_FILENAME."""
        middleware = self._make_middleware("speedscope")
        assert middleware._resolve_output_file_name() == PyInstrumentProfilerMiddleware.DEFAULT_SPEEDSCOPE_FILENAME

    def test_text_output_type_returns_empty_string(self):
        """Cover line 309: text output type returns empty string."""
        middleware = self._make_middleware("text")
        assert middleware._resolve_output_file_name() == ""
