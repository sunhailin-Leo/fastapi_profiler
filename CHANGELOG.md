# ChangeLog

### version 1.5.0
  * **Sampling rate** (`profiler_sample_rate`): profile only a configurable
    fraction of requests (0.0–1.0) to reduce overhead in production.
  * **Error auto-capture** (`always_profile_errors`): 5xx responses are
    always profiled regardless of sampling rate or slow-request threshold.
  * **Structured JSON logging** (`log_format="json"`): emit request log lines
    as JSON objects for log aggregation platforms (ELK, Datadog, etc.).
  * **Stats aggregation** (`StatsCollector`): every request is recorded with
    count, error count, avg/p95/p99/max duration per route.  Exposed via the
    new `/stats` JSON API.
  * **Per-route profile history** (`max_profiles_per_route`): keep the last N
    `ProfileRecord` objects per route in memory for inspection.
  * **Built-in Web UI Dashboard** (`enable_dashboard=True`): mount a
    lightweight HTML dashboard at a configurable path (default
    `/__profiler__`).  Provides `/stats`, `/reset`, and `/config` JSON APIs.
  * **Runtime enable/disable** (`enabled`): toggle profiling without
    restarting the server; also controllable via the dashboard `/config` API.
  * New public exports: `StatsCollector`, `ProfileRecord`, `RouteStats`.
  * New test modules: `test/test_stats.py`, `test/test_dashboard.py`.

### version 1.4.2
  * **Concurrency-safe profiling**: each HTTP request now uses its own
    `Profiler` instance, eliminating session contamination under concurrent
    load.
  * **Path filtering** (`filter_paths`): pass a list of path prefixes to skip
    profiling entirely for matched routes (e.g. `/health`, `/metrics`).
  * **Slow-request threshold** (`slow_request_threshold_ms`): profile output
    is only emitted when the request duration exceeds the given value in
    milliseconds; set to `0` (default) to always emit.
  * **`server_app` is now optional** for all output types.  A `UserWarning`
    is issued instead of raising `RuntimeError` when file-based output types
    are used without a shutdown handler.
  * **Input validation**: `profiler_output_type` is validated at construction
    time and raises `ValueError` for unknown values.
  * **Logging over print**: request log lines are now emitted via
    `logging.getLogger("fastapi_profiler")` instead of `print()`.
  * **Output refactor**: internal file-writing logic is unified in
    `_write_session_to_file()`, eliminating repetitive if/elif chains.
  * Version bump to 1.5.0.

### version 1.4.1
  * fix `AttributeError`[issue#17](https://github.com/sunhailin-Leo/fastapi_profiler/issues/17)

### version 1.4.0
  * add support for speedscope

### version 1.3.0
  * add support for json

### version 1.2.0
  * thanks to [@msnidal](https://github.com/msnidal) Implement `.prof` output

### version 1.1.0
  * Update API `PyInstrumentProfilerMiddleware` to top level, usage like `from fastapi_profiler import PyInstrumentProfilerMiddleware`
  * support `pyinstrument.async_mode`

### version 1.0.0 
  * Finish fastapi middleware with pyinstrument.