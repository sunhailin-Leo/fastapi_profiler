"""Memory profiling sub-package for fastapi_profiler.

This package provides two complementary memory profilers:

- :mod:`tracemalloc_profiler`: standard-library based, zero extra deps,
  supports ``start``/``stop``/``snapshot``/``compare`` operations.
- :mod:`memray_profiler`: optional third-party (Bloomberg ``memray``)
  full native + Python allocation tracker with ``.bin`` output that can
  be rendered with ``memray flamegraph`` / ``memray tree`` CLIs.

Both profilers are exposed via :class:`MemoryProfilerMiddleware`
(see :mod:`fastapi_profiler.memory_middleware`) which mounts an HTTP
control router on the host application.
"""

from fastapi_profiler.memory.memray_profiler import (
    MEMRAY_AVAILABLE,
    MemrayProfiler,
)
from fastapi_profiler.memory.tracemalloc_profiler import TracemallocProfiler

__all__ = [
    "TracemallocProfiler",
    "MemrayProfiler",
    "MEMRAY_AVAILABLE",
]
