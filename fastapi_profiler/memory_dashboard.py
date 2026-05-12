"""HTTP control router for the memory profiler.

Exposes JSON endpoints to drive :class:`TracemallocProfiler` and
:class:`MemrayProfiler`.  Mounted by
:class:`fastapi_profiler.memory_middleware.MemoryProfilerMiddleware` at a
configurable path (default ``/__memory_profiler__``).

Routes
------
GET  /                              Minimal HTML page with usage hints.
GET  /status                        Aggregated status of both profilers.

GET  /tracemalloc/status            tracemalloc status.
POST /tracemalloc/start             {"frames": int?}
POST /tracemalloc/stop
POST /tracemalloc/snapshot          {"top": int?}
POST /tracemalloc/compare           {"top": int?, "snapshot_a": str?, "snapshot_b": str?}
GET  /tracemalloc/snapshots         List captured snapshots.

GET  /memray/status                 memray availability + current session.
POST /memray/start                  {"output_path": str?, "native": bool?, "follow_fork": bool?, "trace_python_allocators": bool?}
POST /memray/stop

No authentication is enforced; protect the mount path via reverse proxy
ACLs or by setting ``filter_paths`` on any other middleware.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, Router

from fastapi_profiler.memory.memray_profiler import (
    MemrayProfiler,
    MemrayUnavailableError,
)
from fastapi_profiler.memory.tracemalloc_profiler import TracemallocProfiler

_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>FastAPI Memory Profiler</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f0f2f5;color:#333;padding:24px;max-width:920px;margin:0 auto}
h1{color:#1a1a2e;margin-bottom:8px;font-size:22px}
h2{color:#2c3e50;margin-top:24px;margin-bottom:8px;font-size:16px}
p{color:#555;margin-bottom:12px}
code{background:#eef1f5;padding:2px 6px;border-radius:4px;font-family:'SF Mono',Menlo,monospace;font-size:13px}
pre{background:#1e1e2e;color:#dcdcdc;padding:12px 14px;border-radius:6px;overflow-x:auto;font-size:12px;line-height:1.5}
ul{margin-left:18px;color:#555;line-height:1.7}
.tag{display:inline-block;background:#3498db;color:#fff;padding:1px 8px;border-radius:3px;font-size:11px;font-weight:600;margin-right:6px}
.tag-post{background:#27ae60}
.tag-get{background:#3498db}
</style>
</head>
<body>
<h1>🧠 FastAPI Memory Profiler</h1>
<p>HTTP control plane for <code>tracemalloc</code> and <code>memray</code>. All endpoints accept/return JSON.</p>

<h2>Status</h2>
<ul>
  <li><span class="tag tag-get">GET</span><code>./status</code> — combined status</li>
</ul>

<h2>tracemalloc</h2>
<ul>
  <li><span class="tag tag-get">GET</span><code>./tracemalloc/status</code></li>
  <li><span class="tag tag-post">POST</span><code>./tracemalloc/start</code> — body: <code>{"frames": 25}</code></li>
  <li><span class="tag tag-post">POST</span><code>./tracemalloc/stop</code></li>
  <li><span class="tag tag-post">POST</span><code>./tracemalloc/snapshot</code> — body: <code>{"top": 20}</code></li>
  <li><span class="tag tag-post">POST</span><code>./tracemalloc/compare</code> — body: <code>{"top": 20}</code></li>
  <li><span class="tag tag-get">GET</span><code>./tracemalloc/snapshots</code></li>
</ul>

<h2>memray (optional)</h2>
<ul>
  <li><span class="tag tag-get">GET</span><code>./memray/status</code></li>
  <li><span class="tag tag-post">POST</span><code>./memray/start</code> — body: <code>{"native": true, "follow_fork": false}</code></li>
  <li><span class="tag tag-post">POST</span><code>./memray/stop</code></li>
</ul>
<p>After stopping a memray session, render the resulting <code>.bin</code> file with:</p>
<pre>memray flamegraph &lt;output_path&gt;
memray tree       &lt;output_path&gt;
memray stats      &lt;output_path&gt;</pre>
</body>
</html>"""


def _bad_request(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=400)


def _service_unavailable(message: str, **extra: Any) -> JSONResponse:
    body: Dict[str, Any] = {"error": message}
    body.update(extra)
    return JSONResponse(body, status_code=503)


async def _read_json_body(request: Request) -> Dict[str, Any] | None:
    """Return parsed JSON dict, or None when body is empty.  Raises ValueError on bad JSON."""
    raw = await request.body()
    if not raw:
        return None
    try:
        import json
        data = json.loads(raw)
    except Exception as exc:
        raise ValueError(f"invalid JSON body: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def create_memory_router(
    tracemalloc_profiler: TracemallocProfiler,
    memray_profiler: MemrayProfiler,
) -> Router:
    """Return a Starlette ``Router`` exposing memory profiler controls."""

    # ------------------------------------------------------------------
    # Index + combined status
    # ------------------------------------------------------------------

    async def get_index(_: Request) -> HTMLResponse:
        return HTMLResponse(content=_INDEX_HTML)

    async def get_combined_status(_: Request) -> JSONResponse:
        return JSONResponse({
            "tracemalloc": tracemalloc_profiler.status(),
            "memray": memray_profiler.status(),
        })

    # ------------------------------------------------------------------
    # tracemalloc routes
    # ------------------------------------------------------------------

    async def tm_status(_: Request) -> JSONResponse:
        return JSONResponse(tracemalloc_profiler.status())

    async def tm_start(request: Request) -> JSONResponse:
        try:
            body = await _read_json_body(request) or {}
        except ValueError as exc:
            return _bad_request(str(exc))

        frames = body.get("frames")
        if frames is not None:
            try:
                frames = int(frames)
            except (TypeError, ValueError):
                return _bad_request("frames must be a positive integer")
            if frames < 1:
                return _bad_request("frames must be a positive integer")

        try:
            return JSONResponse(tracemalloc_profiler.start(frames=frames))
        except ValueError as exc:
            return _bad_request(str(exc))

    async def tm_stop(_: Request) -> JSONResponse:
        return JSONResponse(tracemalloc_profiler.stop())

    async def tm_snapshot(request: Request) -> JSONResponse:
        try:
            body = await _read_json_body(request) or {}
        except ValueError as exc:
            return _bad_request(str(exc))

        top = body.get("top")
        if top is not None:
            try:
                top = int(top)
            except (TypeError, ValueError):
                return _bad_request("top must be a positive integer")
            if top < 1:
                return _bad_request("top must be a positive integer")

        try:
            return JSONResponse(tracemalloc_profiler.snapshot(top=top))
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except ValueError as exc:
            return _bad_request(str(exc))

    async def tm_compare(request: Request) -> JSONResponse:
        try:
            body = await _read_json_body(request) or {}
        except ValueError as exc:
            return _bad_request(str(exc))

        top = body.get("top")
        if top is not None:
            try:
                top = int(top)
            except (TypeError, ValueError):
                return _bad_request("top must be a positive integer")
            if top < 1:
                return _bad_request("top must be a positive integer")

        snapshot_a = body.get("snapshot_a")
        snapshot_b = body.get("snapshot_b")
        if snapshot_a is not None and not isinstance(snapshot_a, str):
            return _bad_request("snapshot_a must be a string")
        if snapshot_b is not None and not isinstance(snapshot_b, str):
            return _bad_request("snapshot_b must be a string")

        try:
            return JSONResponse(tracemalloc_profiler.compare(
                top=top,
                snapshot_a=snapshot_a,
                snapshot_b=snapshot_b,
            ))
        except KeyError as exc:
            return JSONResponse({"error": str(exc).strip("'")}, status_code=404)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except ValueError as exc:
            return _bad_request(str(exc))

    async def tm_list_snapshots(_: Request) -> JSONResponse:
        return JSONResponse({"snapshots": tracemalloc_profiler.list_snapshots()})

    # ------------------------------------------------------------------
    # memray routes
    # ------------------------------------------------------------------

    async def mr_status(_: Request) -> JSONResponse:
        return JSONResponse(memray_profiler.status())

    async def mr_start(request: Request) -> JSONResponse:
        try:
            body = await _read_json_body(request) or {}
        except ValueError as exc:
            return _bad_request(str(exc))

        output_path = body.get("output_path")
        if output_path is not None and not isinstance(output_path, str):
            return _bad_request("output_path must be a string")

        bool_fields = ("native", "follow_fork", "trace_python_allocators")
        kwargs: Dict[str, Any] = {}
        for field_name in bool_fields:
            if field_name in body:
                value = body[field_name]
                if not isinstance(value, bool):
                    return _bad_request(f"{field_name} must be a boolean")
                kwargs[field_name] = value

        try:
            result = memray_profiler.start(output_path=output_path, **kwargs)
            return JSONResponse(result)
        except MemrayUnavailableError as exc:
            return _service_unavailable(
                str(exc),
                availability=memray_profiler.is_available(),
            )
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    async def mr_stop(_: Request) -> JSONResponse:
        return JSONResponse(memray_profiler.stop())

    return Router(routes=[
        Route("/", endpoint=get_index, methods=["GET"]),
        Route("/status", endpoint=get_combined_status, methods=["GET"]),
        Route("/tracemalloc/status", endpoint=tm_status, methods=["GET"]),
        Route("/tracemalloc/start", endpoint=tm_start, methods=["POST"]),
        Route("/tracemalloc/stop", endpoint=tm_stop, methods=["POST"]),
        Route("/tracemalloc/snapshot", endpoint=tm_snapshot, methods=["POST"]),
        Route("/tracemalloc/compare", endpoint=tm_compare, methods=["POST"]),
        Route("/tracemalloc/snapshots", endpoint=tm_list_snapshots, methods=["GET"]),
        Route("/memray/status", endpoint=mr_status, methods=["GET"]),
        Route("/memray/start", endpoint=mr_start, methods=["POST"]),
        Route("/memray/stop", endpoint=mr_stop, methods=["POST"]),
    ])
