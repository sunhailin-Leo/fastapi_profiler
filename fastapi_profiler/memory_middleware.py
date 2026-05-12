"""ASGI middleware exposing memory profiling control endpoints.

This middleware is intentionally separate from
:class:`fastapi_profiler.profiler.PyInstrumentProfilerMiddleware`:

- CPU/wall-time profiling is per-request, sampling-based.
- Memory profiling is process-global and operator-driven (start/stop/dump
  via HTTP).

Pass-through behaviour
----------------------
The middleware does **not** profile or record per-request data.  Its only
runtime job is to short-circuit requests targeted at its own dashboard
path so that they cannot recurse into other middlewares' profiling logic
(e.g. they never appear in pyinstrument profiles).  All other requests
are forwarded unchanged.

Usage
-----
.. code-block:: python

    from fastapi import FastAPI
    from fastapi_profiler import MemoryProfilerMiddleware

    app = FastAPI()
    app.add_middleware(
        MemoryProfilerMiddleware,
        server_app=app,
        snapshot_dir="./mem-snapshots",
        autostart_tracemalloc=False,
    )

    # Then drive it via HTTP:
    #   curl -XPOST http://localhost:8080/__memory_profiler__/tracemalloc/start
    #   curl -XPOST http://localhost:8080/__memory_profiler__/tracemalloc/snapshot
"""

from __future__ import annotations

import warnings
from typing import Any, Callable, Optional

from starlette.types import ASGIApp, Receive, Scope, Send

from fastapi_profiler.memory.memray_profiler import MemrayProfiler
from fastapi_profiler.memory.tracemalloc_profiler import TracemallocProfiler
from fastapi_profiler.memory_dashboard import create_memory_router


class MemoryProfilerMiddleware:
    """ASGI middleware that mounts a memory profiler control router.

    Parameters
    ----------
    app:
        The ASGI application to wrap.
    server_app:
        The host Starlette/FastAPI application.  Required to mount the
        control router and to register a shutdown hook that finalises any
        running memray session.
    memory_dashboard_path:
        URL prefix where the memory profiler endpoints are mounted
        (default ``"/__memory_profiler__"``).
    snapshot_dir:
        Directory used for both tracemalloc ``.snap`` files and memray
        ``.bin`` files (default ``"./mem-snapshots"``).
    tracemalloc_frames:
        Default frame depth passed to ``tracemalloc.start`` when the
        ``/tracemalloc/start`` endpoint is called without an explicit
        ``frames`` value.
    tracemalloc_top:
        Default top-N size for ``snapshot`` / ``compare`` responses.
    autostart_tracemalloc:
        When True, ``tracemalloc.start`` is invoked during middleware
        construction.  Useful when you want to capture allocations from
        application startup as well.  Defaults to False so the middleware
        has zero overhead until explicitly activated.
    """

    DEFAULT_PATH = "/__memory_profiler__"
    DEFAULT_SNAPSHOT_DIR = "./mem-snapshots"

    def __init__(
        self,
        app: ASGIApp,
        *,
        server_app=None,
        memory_dashboard_path: str = DEFAULT_PATH,
        snapshot_dir: str = DEFAULT_SNAPSHOT_DIR,
        tracemalloc_frames: int = TracemallocProfiler.DEFAULT_FRAMES,
        tracemalloc_top: int = TracemallocProfiler.DEFAULT_TOP,
        autostart_tracemalloc: bool = False,
    ) -> None:
        self.app = app
        self._dashboard_path = memory_dashboard_path.rstrip("/") or self.DEFAULT_PATH
        self._snapshot_dir = snapshot_dir

        self._tracemalloc = TracemallocProfiler(
            snapshot_dir=snapshot_dir,
            default_frames=tracemalloc_frames,
            default_top=tracemalloc_top,
        )
        self._memray = MemrayProfiler(output_dir=snapshot_dir)

        if server_app is None:
            warnings.warn(
                "MemoryProfilerMiddleware requires server_app to mount the "
                "control router.  The HTTP endpoints will not be available; "
                "pass server_app=app to enable them.",
                UserWarning,
                stacklevel=2,
            )
        else:
            router = create_memory_router(
                tracemalloc_profiler=self._tracemalloc,
                memray_profiler=self._memray,
            )
            server_app.mount(self._dashboard_path, app=router)
            # Best-effort cleanup of an active memray session on shutdown
            # so that the .bin file is finalised even if the operator
            # forgets to call /memray/stop.
            server_app.router.on_event("shutdown")(self._on_shutdown)

        if autostart_tracemalloc:
            self._tracemalloc.start(frames=tracemalloc_frames)

    # ------------------------------------------------------------------
    # ASGI entry point
    # ------------------------------------------------------------------

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # We want this middleware to be transparent for everything that's
        # not the memory dashboard itself.  The dashboard router is mounted
        # directly on the host app, so we simply forward all requests.
        await self.app(scope, receive, send)

    # ------------------------------------------------------------------
    # Accessors (used by tests and advanced integrations)
    # ------------------------------------------------------------------

    @property
    def tracemalloc_profiler(self) -> TracemallocProfiler:
        return self._tracemalloc

    @property
    def memray_profiler(self) -> MemrayProfiler:
        return self._memray

    @property
    def dashboard_path(self) -> str:
        return self._dashboard_path

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    async def _on_shutdown(self) -> None:
        # Finalise any running memray session so the capture file is valid.
        try:
            if self._memray.is_running():
                self._memray.stop()
        except Exception:  # noqa: BLE001 - shutdown must never raise
            pass
        # tracemalloc cleanup is cheap and idempotent.
        try:
            if self._tracemalloc.is_running():
                self._tracemalloc.stop()
        except Exception:  # noqa: BLE001
            pass

    # Compatibility shim so users can register the hook manually if they
    # constructed the middleware without ``server_app``.
    def get_shutdown_handler(self) -> Callable[[], Any] | None:
        return self._on_shutdown
