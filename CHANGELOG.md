# ChangeLog

### version 1.6.0
  * **Memory profiler middleware** (`MemoryProfilerMiddleware`): a new,
    independent ASGI middleware that exposes an HTTP control plane for
    memory analysis.  Mounted at `/__memory_profiler__` by default.
  * **`tracemalloc` integration** (standard library, zero extra deps):
    drive the full lifecycle over HTTP — `start`, `stop`, `snapshot`,
    `compare`, `snapshots` (list), `status`.  Snapshots are persisted as
    `.snap` files under a configurable `snapshot_dir`.
    - `autostart_tracemalloc=True` begins tracing during middleware
      construction so allocations made at application startup are
      captured as well.
    - Configurable frame depth (`tracemalloc_frames`, default 25) and
      top-N size (`tracemalloc_top`, default 20).
  * **`memray` integration** (optional dependency, Linux/macOS only):
    drive `memray.Tracker` start/stop via HTTP and obtain a `.bin`
    capture file ready to be rendered by the official `memray flamegraph`
    / `memray tree` / `memray stats` CLIs.  Supports `native_traces`,
    `follow_fork`, and `trace_python_allocators` flags.
    - Install with `pip install 'fastapi_profiler[memray]'`.
    - Returns a structured `503 Service Unavailable` (with reason and
      import error) on platforms where memray is missing or unsupported.
  * **Graceful shutdown handling**: any active memray session is
    finalised automatically on application shutdown so the `.bin` file
    is never left half-written.
  * **No built-in authentication** — the dashboard mount path is not
    protected on purpose; secure it via reverse-proxy ACLs or by setting
    `filter_paths` on other middlewares.  The new
    `fastapi_memory_custom_path_example.py` documents recommended
    deployment patterns.
  * **New public exports**: `MemoryProfilerMiddleware`,
    `TracemallocProfiler`, `MemrayProfiler`, `MEMRAY_AVAILABLE`.
  * **Examples** (under `example/`):
    - `fastapi_memory_profiler_example.py` — multi-scenario allocation
      routes (`/alloc/small`, `/alloc/big`, `/alloc/leak`,
      `/alloc/clear`, `/alloc/stats`).
    - `fastapi_memory_tracemalloc_client_example.py` — stdlib-only
      driver that walks the full tracemalloc lifecycle and prints all
      `.snap` files on disk.
    - `fastapi_memory_memray_client_example.py` — stdlib-only driver
      for the memray lifecycle that prints the resulting `.bin` path
      and the matching `memray flamegraph/tree/stats` commands.
    - `fastapi_cpu_and_memory_profiler_example.py` — CPU + memory
      profilers running side-by-side with `filter_paths` isolation.
    - `fastapi_memory_autostart_example.py` — capture allocations made
      during application startup via `autostart_tracemalloc=True`.
    - `fastapi_memory_custom_path_example.py` — custom dashboard path
      and reverse-proxy protection guidance.
  * **Tests**: `test/test_memory.py` and `test/test_memory_coverage.py`
    bring the memory subsystem to ~99% line coverage, including
    Windows/unavailable branches, tracker rollback on start failure,
    and full HTTP routing through the dashboard.

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