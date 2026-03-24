"""Core profiler middleware for fastapi_profiler.

This module provides the ASGI middleware that profiles HTTP requests using
pyinstrument (or cProfile for the 'prof' output type).

New in 1.5.0
------------
- **Sampling rate** (``profiler_sample_rate``): profile only a fraction of
  requests (0.0–1.0).  Useful for production deployments.
- **Error auto-capture** (``always_profile_errors``): 5xx responses are
  always profiled regardless of sampling rate or slow-request threshold.
- **Structured JSON logging** (``log_format``): emit request log lines as
  JSON for log aggregation platforms.
- **Stats aggregation**: every request is recorded in a
  :class:`~fastapi_profiler.stats.StatsCollector` instance.
- **Per-route profile history** (``max_profiles_per_route``): keep the last
  N profile records per route in memory.
- **Built-in Web UI Dashboard** (``enable_dashboard``): mount a lightweight
  HTML dashboard at a configurable path.
- **Runtime enable/disable** (``enabled``): toggle profiling without
  restarting the server.
"""

import cProfile
import json
import pstats
import random
import threading
import time
import warnings
from io import StringIO
from logging import getLogger
from typing import List, Literal, Optional, cast

from pyinstrument import Profiler
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fastapi_profiler.dashboard import create_dashboard_router
from fastapi_profiler.stats import StatsCollector

logger = getLogger("fastapi_profiler")

# ---------------------------------------------------------------------------
# Output type constants
# ---------------------------------------------------------------------------
OUTPUT_TYPE_TEXT = "text"
OUTPUT_TYPE_HTML = "html"
OUTPUT_TYPE_PROF = "prof"
OUTPUT_TYPE_JSON = "json"
OUTPUT_TYPE_SPEEDSCOPE = "speedscope"

VALID_OUTPUT_TYPES = {
    OUTPUT_TYPE_TEXT,
    OUTPUT_TYPE_HTML,
    OUTPUT_TYPE_PROF,
    OUTPUT_TYPE_JSON,
    OUTPUT_TYPE_SPEEDSCOPE,
}

LOG_FORMAT_TEXT = "text"
LOG_FORMAT_JSON = "json"
VALID_LOG_FORMATS = {LOG_FORMAT_TEXT, LOG_FORMAT_JSON}


class PyInstrumentProfilerMiddleware:
    """ASGI middleware that profiles each HTTP request using pyinstrument.

    Parameters
    ----------
    app:
        The ASGI application to wrap.
    server_app:
        Optional Starlette/FastAPI application used to register the shutdown
        event handler.  Required when ``enable_dashboard=True`` so the
        dashboard router can be mounted.
    profiler_interval:
        pyinstrument sampling interval in seconds (default ``0.0001``).
    profiler_output_type:
        One of ``"text"``, ``"html"``, ``"prof"``, ``"json"``,
        ``"speedscope"`` (default ``"text"``).
    is_print_each_request:
        When ``True`` (default) log profile output after every request.
    async_mode:
        pyinstrument async mode (default ``"enabled"``).
    html_file_name:
        Output file path for ``profiler_output_type="html"``.
    prof_file_name:
        Output file path for ``profiler_output_type`` in
        ``{"prof", "json", "speedscope"}``.
    open_in_browser:
        Open the HTML profile in the default browser on shutdown.
    filter_paths:
        List of path prefixes to skip profiling entirely.
    slow_request_threshold_ms:
        Only emit profile output when request duration exceeds this value
        (milliseconds).  ``0`` means always emit.
    profiler_sample_rate:
        Fraction of requests to profile (``0.0``–``1.0``).  ``1.0`` means
        profile every request.  Requests that are not sampled still have
        their timing recorded in the stats collector.
    always_profile_errors:
        When ``True`` (default), 5xx responses are always profiled
        regardless of ``profiler_sample_rate`` or
        ``slow_request_threshold_ms``.
    log_format:
        ``"text"`` (default) or ``"json"``.  JSON mode emits structured log
        lines suitable for log aggregation platforms.
    max_profiles_per_route:
        Maximum number of profile records to keep in memory per route
        (default ``10``).
    enable_dashboard:
        Mount a built-in Web UI dashboard.  Requires ``server_app``.
    dashboard_path:
        URL prefix for the dashboard (default ``"/__profiler__"``).
    enabled:
        Master switch.  When ``False`` the middleware passes all requests
        through without any profiling overhead.
    """

    DEFAULT_HTML_FILENAME = "./fastapi-profiler.html"
    DEFAULT_PROF_FILENAME = "./fastapi-profiler.prof"
    DEFAULT_JSON_FILENAME = "./fastapi-profiler.json"
    DEFAULT_SPEEDSCOPE_FILENAME = "./fastapi-profiler-speedscope.json"

    def __init__(
        self,
        app: ASGIApp,
        *,
        server_app=None,
        profiler_interval: float = 0.0001,
        profiler_output_type: str = OUTPUT_TYPE_TEXT,
        is_print_each_request: bool = True,
        async_mode: str = "enabled",
        html_file_name: Optional[str] = None,
        prof_file_name: Optional[str] = None,
        open_in_browser: bool = False,
        filter_paths: Optional[List[str]] = None,
        slow_request_threshold_ms: float = 0,
        profiler_sample_rate: float = 1.0,
        always_profile_errors: bool = True,
        log_format: str = LOG_FORMAT_TEXT,
        max_profiles_per_route: int = 10,
        enable_dashboard: bool = False,
        dashboard_path: str = "/__profiler__",
        enabled: bool = True,
        **profiler_kwargs,
    ):
        if profiler_output_type not in VALID_OUTPUT_TYPES:
            raise ValueError(
                f"Invalid profiler_output_type {profiler_output_type!r}. "
                f"Must be one of: {sorted(VALID_OUTPUT_TYPES)}"
            )
        if log_format not in VALID_LOG_FORMATS:
            raise ValueError(
                f"Invalid log_format {log_format!r}. "
                f"Must be one of: {sorted(VALID_LOG_FORMATS)}"
            )
        if not (0.0 <= profiler_sample_rate <= 1.0):
            raise ValueError(
                f"profiler_sample_rate must be between 0.0 and 1.0, "
                f"got {profiler_sample_rate!r}"
            )

        # Warn when file-based outputs have no shutdown hook.
        file_based_output_types = {
            OUTPUT_TYPE_HTML,
            OUTPUT_TYPE_JSON,
            OUTPUT_TYPE_SPEEDSCOPE,
            OUTPUT_TYPE_PROF,
        }
        if profiler_output_type in file_based_output_types and server_app is None:
            warnings.warn(
                f"profiler_output_type={profiler_output_type!r} writes results on "
                "server shutdown, but no server_app was provided.  Pass "
                "server_app=app to register the shutdown handler automatically, "
                "or call middleware.get_profiler_result() manually.",
                UserWarning,
                stacklevel=2,
            )

        if enable_dashboard and server_app is None:
            warnings.warn(
                "enable_dashboard=True requires server_app to mount the dashboard "
                "router.  The dashboard will not be available.",
                UserWarning,
                stacklevel=2,
            )

        self.app = app
        self._output_type = profiler_output_type
        self._log_each_request = is_print_each_request
        self._html_file_name: Optional[str] = html_file_name
        self._prof_file_name: Optional[str] = prof_file_name
        self._open_in_browser: bool = open_in_browser
        self._profiler_kwargs: dict = profiler_kwargs
        self._filter_paths: List[str] = filter_paths or []
        self._slow_request_threshold_ms: float = slow_request_threshold_ms
        self._profiler_interval = profiler_interval
        self._async_mode = async_mode
        self._sample_rate: float = profiler_sample_rate
        self._always_profile_errors: bool = always_profile_errors
        self._log_format: str = log_format
        self._enabled: bool = enabled

        # Stats collector shared across all requests.
        self._stats_collector = StatsCollector(
            max_profiles_per_route=max_profiles_per_route
        )

        # cProfile accumulator (only for "prof" output type).
        # Per-request profiles are merged here under _cprofile_lock so that
        # concurrent ASGI requests each use their own cProfile.Profile instance
        # and then safely contribute to the aggregated stats for dump_stats().
        if profiler_output_type == OUTPUT_TYPE_PROF:
            self._cprofile_lock = threading.Lock()
            self._cprofile_stats: Optional[pstats.Stats] = None

        # Register shutdown handler via the router (compatible with all
        # FastAPI versions including those that deprecated add_event_handler).
        if server_app is not None:
            server_app.router.on_event("shutdown")(self.get_profiler_result)

        # Mount dashboard router.
        if enable_dashboard and server_app is not None:
            dashboard_router = create_dashboard_router(
                stats_collector=self._stats_collector,
                get_enabled=lambda: self._enabled,
                set_enabled=self._set_enabled,
                get_config=self._get_runtime_config,
                set_config=self._apply_runtime_config,
            )
            server_app.mount(dashboard_path, app=dashboard_router)

    # ------------------------------------------------------------------
    # Runtime config helpers (used by the dashboard)
    # ------------------------------------------------------------------

    def _set_enabled(self, value: bool) -> None:
        self._enabled = value

    def _get_runtime_config(self) -> dict:
        return {
            "sample_rate": self._sample_rate,
            "slow_request_threshold_ms": self._slow_request_threshold_ms,
        }

    def _apply_runtime_config(self, config: dict) -> None:
        if "sample_rate" in config:
            sample_rate = float(config["sample_rate"])
            if not 0.0 <= sample_rate <= 1.0:
                raise ValueError("sample_rate must be between 0.0 and 1.0")
            self._sample_rate = sample_rate
        if "slow_request_threshold_ms" in config:
            slow_request_threshold_ms = float(config["slow_request_threshold_ms"])
            if slow_request_threshold_ms < 0.0:
                raise ValueError(
                    "slow_request_threshold_ms must be a non-negative number"
                )
            self._slow_request_threshold_ms = slow_request_threshold_ms

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_path_filtered(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self._filter_paths)

    def _should_sample(self) -> bool:
        """Return True if this request should be profiled based on sample rate."""
        if self._sample_rate >= 1.0:
            return True
        if self._sample_rate <= 0.0:
            return False
        return random.random() < self._sample_rate

    def _make_pyinstrument_profiler(self) -> Profiler:
        async_mode = cast(
            Literal["enabled", "disabled", "strict"],
            self._async_mode,
        )
        return Profiler(
            interval=self._profiler_interval,
            async_mode=async_mode,
        )

    def _get_profile_output(self, profiler: Profiler) -> str:
        """Return profile output string in the format matching *output_type*."""
        if self._output_type == OUTPUT_TYPE_HTML:
            return profiler.output_html()
        if self._output_type in {OUTPUT_TYPE_JSON, OUTPUT_TYPE_SPEEDSCOPE}:
            from pyinstrument.renderers import JSONRenderer, SpeedscopeRenderer

            renderer = (
                JSONRenderer()
                if self._output_type == OUTPUT_TYPE_JSON
                else SpeedscopeRenderer()
            )
            return profiler.output(renderer=renderer)
        return profiler.output_text(**self._profiler_kwargs)

    def _write_profile_to_file(self, content: str) -> None:
        """Write *content* to the configured output file (overwriting each time)."""
        file_path = self._resolve_output_file_name()
        if not file_path:
            return
        try:
            with open(file_path, "w") as fh:
                fh.write(content)
        except OSError as exc:
            logger.error("Failed to write profile to %r: %s", file_path, exc)

    def _emit_request_log(
        self,
        method: str,
        path: str,
        duration_ms: float,
        status_code: int,
    ) -> None:
        if self._log_format == LOG_FORMAT_JSON:
            log_record = {
                "logger": "fastapi_profiler",
                "method": method,
                "path": path,
                "duration_ms": round(duration_ms, 3),
                "status_code": status_code,
            }
            logger.info(json.dumps(log_record))
        else:
            logger.info(
                "Method: %s, Path: %s, Duration: %.3fms, Status: %s",
                method,
                path,
                duration_ms,
                status_code,
            )

    def _resolve_output_file_name(self) -> str:
        if self._output_type == OUTPUT_TYPE_HTML:
            return self._html_file_name or self.DEFAULT_HTML_FILENAME
        if self._output_type == OUTPUT_TYPE_PROF:
            return self._prof_file_name or self.DEFAULT_PROF_FILENAME
        if self._output_type == OUTPUT_TYPE_JSON:
            return self._prof_file_name or self.DEFAULT_JSON_FILENAME
        if self._output_type == OUTPUT_TYPE_SPEEDSCOPE:
            return self._prof_file_name or self.DEFAULT_SPEEDSCOPE_FILENAME
        return ""

    # ------------------------------------------------------------------
    # ASGI entry point
    # ------------------------------------------------------------------

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        method = request.method
        path = request.url.path

        # Master switch: pass through without any overhead.
        if not self._enabled:
            await self.app(scope, receive, send)
            return

        # Skip profiling for filtered paths.
        if self._is_path_filtered(path):
            await self.app(scope, receive, send)
            return

        if self._output_type == OUTPUT_TYPE_PROF:
            await self._call_with_cprofile(scope, receive, send, method, path)
        else:
            await self._call_with_pyinstrument(scope, receive, send, method, path)

    # ------------------------------------------------------------------
    # Per-request profiling implementations
    # ------------------------------------------------------------------

    async def _call_with_pyinstrument(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        method: str,
        path: str,
    ) -> None:
        status_code = 500

        async def wrapped_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                nonlocal status_code
                status_code = message["status"]
            await send(message)

        should_sample = self._should_sample()
        # When always_profile_errors is True we must start the profiler for
        # every request so that we can capture a profile even for requests
        # that would have been skipped by the sampling decision.
        start_profiler = should_sample or self._always_profile_errors
        profiler = self._make_pyinstrument_profiler() if start_profiler else None

        begin = time.perf_counter()
        if profiler:
            profiler.start()
        try:
            await self.app(scope, receive, wrapped_send)
        finally:
            if profiler:
                profiler.stop()
            duration_ms = (time.perf_counter() - begin) * 1000

            is_error = status_code >= 500
            force_profile = is_error and self._always_profile_errors

            # Decide whether to emit profile output.
            exceeds_threshold = (
                self._slow_request_threshold_ms <= 0
                or duration_ms >= self._slow_request_threshold_ms
            )
            # Emit when: the request was sampled AND exceeds the threshold,
            # OR when we must force-profile due to an error.
            emit_profile = profiler is not None and (
                (should_sample and exceeds_threshold) or force_profile
            )

            profile_output: Optional[str] = None
            if emit_profile and profiler is not None:
                profile_output = self._get_profile_output(profiler)
                # For file-based output types, write/overwrite the file now.
                if self._output_type in {
                    OUTPUT_TYPE_HTML,
                    OUTPUT_TYPE_JSON,
                    OUTPUT_TYPE_SPEEDSCOPE,
                }:
                    self._write_profile_to_file(profile_output)

            if self._log_each_request:
                self._emit_request_log(method, path, duration_ms, status_code)
                if emit_profile and profile_output:
                    # For text output, log the profile inline.  For file-based
                    # types log the path instead to keep logs readable.
                    if self._output_type == OUTPUT_TYPE_TEXT:
                        logger.info(profile_output)
                    else:
                        logger.info(
                            "Profile written to %r",
                            self._resolve_output_file_name(),
                        )

            # Always record stats regardless of sampling.
            await self._stats_collector.record(
                path=path,
                method=method,
                duration_ms=duration_ms,
                status_code=status_code,
                profile_output=profile_output
                if self._output_type == OUTPUT_TYPE_TEXT
                else None,
            )

    async def _call_with_cprofile(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        method: str,
        path: str,
    ) -> None:
        status_code = 500

        async def wrapped_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                nonlocal status_code
                status_code = message["status"]
            await send(message)

        should_sample = self._should_sample()
        # Always start a per-request profiler when always_profile_errors is set
        # so that 5xx responses can be captured even when sampling says no.
        start_profiler = should_sample or self._always_profile_errors
        per_request_profile = cProfile.Profile() if start_profiler else None

        begin = time.perf_counter()
        if per_request_profile:
            per_request_profile.enable()
        try:
            await self.app(scope, receive, wrapped_send)
        finally:
            if per_request_profile:
                per_request_profile.disable()
            duration_ms = (time.perf_counter() - begin) * 1000

            is_error = status_code >= 500
            force_profile = is_error and self._always_profile_errors
            exceeds_threshold = (
                self._slow_request_threshold_ms <= 0
                or duration_ms >= self._slow_request_threshold_ms
            )
            emit_profile = per_request_profile is not None and (
                (should_sample and exceeds_threshold) or force_profile
            )

            if self._log_each_request:
                self._emit_request_log(method, path, duration_ms, status_code)
                if emit_profile and per_request_profile is not None:
                    stats_buffer = StringIO()
                    pstats.Stats(
                        per_request_profile, stream=stats_buffer
                    ).print_stats()
                    logger.info(stats_buffer.getvalue())

            # Merge per-request stats into the shared accumulator under a lock
            # so concurrent ASGI requests don't corrupt each other's data.
            if per_request_profile is not None:
                with self._cprofile_lock:
                    if self._cprofile_stats is None:
                        self._cprofile_stats = pstats.Stats(per_request_profile)
                    else:
                        self._cprofile_stats.add(per_request_profile)

            await self._stats_collector.record(
                path=path,
                method=method,
                duration_ms=duration_ms,
                status_code=status_code,
            )

    # ------------------------------------------------------------------
    # Shutdown handler
    # ------------------------------------------------------------------

    async def get_profiler_result(self) -> None:
        """Flush the accumulated profile to the configured output destination."""
        if self._output_type == OUTPUT_TYPE_TEXT:
            logger.info(
                "fastapi_profiler: text output is per-request; "
                "no aggregated session to flush on shutdown."
            )
            return

        if self._output_type == OUTPUT_TYPE_PROF:
            with self._cprofile_lock:
                if self._cprofile_stats is None:
                    logger.info(
                        "fastapi_profiler: no cProfile data accumulated yet."
                    )
                    return
                file_path = self._resolve_output_file_name()
                logger.info("Dumping cProfile stats to %r", file_path)
                self._cprofile_stats.dump_stats(file_path)
            logger.info("Done writing profile to %r", file_path)
            return

        # html / json / speedscope: profiles are written per-request so there
        # is nothing to flush on shutdown.
        logger.info(
            "fastapi_profiler: %r profiles are written per-request to %r.",
            self._output_type,
            self._resolve_output_file_name(),
        )
