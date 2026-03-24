<h1 align="center">fastapi_profiler</h1>
<p align="center">
    <em>A FastAPI Middleware of joerick/pyinstrument to check your service performance.</em>
</p>
<p align="center">
    <a href="https://codecov.io/gh/sunhailin-Leo/fastapi_profiler">
        <img src="https://codecov.io/gh/sunhailin-Leo/fastapi_profiler/branch/main/graph/badge.svg" alt="Codecov">
    </a>
    <a href="https://img.shields.io/pypi/v/fastapi_profiler">
        <img src="https://img.shields.io/pypi/v/fastapi_profiler.svg" alt="Package version">
    </a>
    <a href="https://pypi.org/project/fastapi_profiler/">
        <img src="https://img.shields.io/pypi/pyversions/fastapi_profiler.svg?colorB=brightgreen" alt="PyPI - Python Version">
    </a>
</p>

<p align="center">
    <a href="https://pypi.org/project/fastapi_profiler">
        <img src="https://img.shields.io/pypi/format/fastapi_profiler.svg" alt="PyPI - Format">
    </a>
    <a href="https://github.com/sunhailin-LEO/fastapi_profiler/pulls">
        <img src="https://img.shields.io/badge/contributions-welcome-brightgreen.svg?style=flat" alt="Contributions welcome">
    </a>
    <a href="https://opensource.org/licenses/MIT">
        <img src="https://img.shields.io/badge/License-MIT-brightgreen.svg" alt="License">
    </a>
</p>

## 📣 Info

A FastAPI Middleware of [pyinstrument](https://github.com/joerick/pyinstrument) to check your service code performance.  
Supports per-request profiling, sampling rate control, structured JSON logging, a built-in Web UI Dashboard, per-route profile history, runtime enable/disable, and request statistics aggregation (p95/p99).

## 🔰 Installation

**Use uv (recommended)**
```shell
$ uv add fastapi_profiler
```

**Use pip**
```shell
$ pip install fastapi_profiler -U
```

## 📝 Quick Start

```python
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi_profiler import PyInstrumentProfilerMiddleware

app = FastAPI()
app.add_middleware(PyInstrumentProfilerMiddleware)

@app.get("/test")
async def normal_request():
    return JSONResponse({"retMsg": "Hello World!"})

if __name__ == "__main__":
    uvicorn.run(app=app, host="0.0.0.0", port=8080, workers=1)
```

## ⚙️ Configuration Reference

All parameters are passed as keyword arguments to `add_middleware()`.

### Core Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `server_app` | `FastAPI \| None` | `None` | Pass the FastAPI app instance to register a shutdown handler that writes file-based output automatically. Required for `html`, `prof`, `json`, `speedscope` output types. |
| `profiler_output_type` | `str` | `"text"` | Output format. One of `"text"`, `"html"`, `"prof"`, `"json"`, `"speedscope"`. |
| `is_print_each_request` | `bool` | `True` | Print/log the profile summary after every request. |
| `profiler_interval` | `float` | `0.0001` | pyinstrument sampling interval in seconds. |
| `async_mode` | `str` | `"enabled"` | pyinstrument async mode. |
| `html_file_name` | `str \| None` | `"./fastapi-profiler.html"` | Output file name for `html` type. |
| `prof_file_name` | `str \| None` | `"./fastapi-profiler.prof"` | Output file name for `prof`, `json`, and `speedscope` types. |
| `open_in_browser` | `bool` | `False` | Automatically open the HTML report in a browser on shutdown. |
| `filter_paths` | `list[str] \| None` | `None` | List of URL path prefixes to skip profiling entirely (e.g. `["/health", "/metrics"]`). |

### 1.5.0 New Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `slow_request_threshold_ms` | `float` | `0` | Only emit profile output when request duration exceeds this value in milliseconds. `0` means always emit. |
| `profiler_sample_rate` | `float` | `1.0` | Fraction of requests to profile (`0.0` – `1.0`). Useful for reducing overhead in production. |
| `always_profile_errors` | `bool` | `True` | Always profile 5xx responses regardless of `profiler_sample_rate` or `slow_request_threshold_ms`. |
| `log_format` | `str` | `"text"` | Log format for request lines. `"text"` emits a human-readable string; `"json"` emits a structured JSON object. |
| `max_profiles_per_route` | `int` | `10` | Maximum number of `ProfileRecord` objects to keep in memory per route (rolling window). |
| `enable_dashboard` | `bool` | `False` | Mount a built-in Web UI Dashboard with stats and runtime control APIs. |
| `dashboard_path` | `str` | `"/__profiler__"` | URL prefix for the dashboard. Only used when `enable_dashboard=True`. |
| `enabled` | `bool` | `True` | Master switch. When `False`, the middleware passes all requests through without profiling. Controllable at runtime via the `/config` API. |

## 🚀 Usage Examples

### Basic — print profile to stdout

```python
app.add_middleware(PyInstrumentProfilerMiddleware)
```

### Output to HTML file

Each sampled request that exceeds the slow-request threshold writes (or
overwrites) the configured HTML file with the latest call-tree profile:

```python
app.add_middleware(
    PyInstrumentProfilerMiddleware,
    server_app=app,
    profiler_output_type="html",
    is_print_each_request=False,
    html_file_name="./fastapi-profiler.html",
)
```

> **Note:** The file is updated on every qualifying request; it always
> contains the **most recent** profile.  Use `profiler_output_type="text"`
> with `is_print_each_request=True` if you want a log entry per request.

### Sampling rate — profile only 10% of requests

```python
app.add_middleware(
    PyInstrumentProfilerMiddleware,
    server_app=app,
    profiler_sample_rate=0.1,   # Profile ~10% of requests
)
```

### Always profile errors — even with sampling disabled

```python
app.add_middleware(
    PyInstrumentProfilerMiddleware,
    server_app=app,
    profiler_sample_rate=0.0,   # Normally profile nothing...
    always_profile_errors=True, # ...but always profile 5xx responses
)
```

### Slow-request threshold — only profile requests slower than 200 ms

```python
app.add_middleware(
    PyInstrumentProfilerMiddleware,
    server_app=app,
    slow_request_threshold_ms=200,
)
```

### Structured JSON logging

```python
import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")

app.add_middleware(
    PyInstrumentProfilerMiddleware,
    server_app=app,
    log_format="json",  # {"logger": "fastapi_profiler", "method": "GET", ...}
)
```

### Built-in Web UI Dashboard

```python
app.add_middleware(
    PyInstrumentProfilerMiddleware,
    server_app=app,
    enable_dashboard=True,
    dashboard_path="/__profiler__",
    filter_paths=["/__profiler__"],  # Exclude dashboard from stats
)
```

After starting the server, open `http://localhost:8080/__profiler__` in your browser.

#### Dashboard API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/__profiler__/` | HTML dashboard UI |
| `GET` | `/__profiler__/stats` | JSON stats for all routes |
| `POST` | `/__profiler__/reset` | Clear all collected stats |
| `POST` | `/__profiler__/config` | Update runtime configuration |

**`/stats` response shape:**

```json
{
  "enabled": true,
  "sample_rate": 1.0,
  "slow_request_threshold_ms": 0,
  "routes": [
    {
      "path": "/test",
      "method": "GET",
      "count": 42,
      "error_count": 1,
      "avg_duration_ms": 3.14,
      "p95_duration_ms": 8.20,
      "p99_duration_ms": 12.50,
      "max_duration_ms": 15.00
    }
  ]
}
```

**`/config` request body (all fields optional):**

```json
{
  "enabled": false,
  "sample_rate": 0.5,
  "slow_request_threshold_ms": 200
}
```

### Runtime enable/disable

```python
app.add_middleware(
    PyInstrumentProfilerMiddleware,
    server_app=app,
    enabled=True,
    enable_dashboard=True,
)
```

Toggle profiling without restarting the server:

```shell
# Disable profiling
curl -X POST http://localhost:8080/__profiler__/config \
     -H "Content-Type: application/json" \
     -d '{"enabled": false}'

# Re-enable with 50% sampling
curl -X POST http://localhost:8080/__profiler__/config \
     -H "Content-Type: application/json" \
     -d '{"enabled": true, "sample_rate": 0.5}'
```

### Per-route profile history

```python
app.add_middleware(
    PyInstrumentProfilerMiddleware,
    server_app=app,
    enable_dashboard=True,
    max_profiles_per_route=20,  # Keep last 20 profiles per route
)
```

### All features combined

```python
app.add_middleware(
    PyInstrumentProfilerMiddleware,
    server_app=app,
    # Sampling & thresholds
    profiler_sample_rate=0.5,
    always_profile_errors=True,
    slow_request_threshold_ms=100,
    # Logging
    is_print_each_request=True,
    log_format="json",
    # Dashboard & stats
    enable_dashboard=True,
    dashboard_path="/__profiler__",
    max_profiles_per_route=10,
    filter_paths=["/__profiler__"],
    # Runtime toggle
    enabled=True,
)
```

## 📂 Example Files

| File | Description |
|------|-------------|
| [`fastapi_example.py`](example/fastapi_example.py) | Minimal setup — print to stdout |
| [`fastapi_to_html_example.py`](example/fastapi_to_html_example.py) | Output to HTML file |
| [`fastapi_to_json_example.py`](example/fastapi_to_json_example.py) | Output to JSON file |
| [`fastapi_to_prof_example.py`](example/fastapi_to_prof_example.py) | Output to `.prof` file |
| [`fastapi_to_speedscope_example.py`](example/fastapi_to_speedscope_example.py) | Output to Speedscope JSON |
| [`fastapi_sampling_rate_example.py`](example/fastapi_sampling_rate_example.py) | Sampling rate control |
| [`fastapi_always_profile_errors_example.py`](example/fastapi_always_profile_errors_example.py) | Always profile 5xx errors |
| [`fastapi_json_logging_example.py`](example/fastapi_json_logging_example.py) | Structured JSON logging |
| [`fastapi_stats_dashboard_example.py`](example/fastapi_stats_dashboard_example.py) | Web UI Dashboard + stats API |
| [`fastapi_per_route_history_example.py`](example/fastapi_per_route_history_example.py) | Per-route profile history |
| [`fastapi_runtime_toggle_example.py`](example/fastapi_runtime_toggle_example.py) | Runtime enable/disable |
| [`fastapi_full_features_example.py`](example/fastapi_full_features_example.py) | All features combined |

## ⛏ Development

### Setup

This project is managed with [uv](https://github.com/astral-sh/uv). Install all dependencies (including dev tools) with:

```shell
$ uv sync --group dev
```

### Common Tasks

Use `make` (Linux/macOS) or `make.bat` (Windows) for common development tasks:

| Command | Description |
|---|---|
| `make install` | Install all dependencies |
| `make lint` | Run ruff + flake8 linters |
| `make typecheck` | Run ty type checker |
| `make test` | Run pytest with coverage |
| `make check` | Run lint + typecheck + test |
| `make build` | Build distribution packages |
| `make publish` | Build and publish to PyPI |
| `make clean` | Remove build artifacts |

### Code Style

This project uses the following tools to ensure code quality:

- **[ruff](https://github.com/astral-sh/ruff)** — fast linter and formatter
- **[flake8](http://flake8.pycqa.org/en/latest/index.html)** — style guide enforcement
- **[ty](https://github.com/astral-sh/ty)** — fast Python type checker
- **[Codecov](https://codecov.io/)** — test coverage reporting

### CI

GitHub Actions runs the full matrix across Python **3.8 – 3.14** on Ubuntu, macOS, and Windows.

## 💡 Author

* [@sunhailin-Leo](https://github.com/sunhailin-Leo)

## 📃 License

MIT [©sunhailin-Leo](https://github.com/sunhailin-Leo)