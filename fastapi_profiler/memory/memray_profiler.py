"""memray-based memory profiler (optional dependency).

`memray <https://github.com/bloomberg/memray>`_ is a native allocation
tracker that produces ``.bin`` capture files which can be rendered by the
``memray`` CLI (``memray flamegraph``, ``memray tree``, ``memray stats``).

Constraints
-----------
- ``memray`` is **not available on Windows** (Linux + macOS only).
- A single Python process can have **at most one** active
  :class:`memray.Tracker` at a time.  This wrapper enforces that with a
  process-wide lock and an internal state machine.
- Tracking is **process-global**: every allocation in the process is
  recorded for the lifetime of the session, regardless of which thread or
  request triggered it.

Lifecycle
---------
1. ``start(...)`` — instantiate ``memray.Tracker`` and enter its context.
2. ``stop()`` — exit the tracker context which finalises the ``.bin`` file.
3. ``status()`` — query current state.
"""

from __future__ import annotations

import os
import platform
import sys
import threading
import time
import uuid
from typing import Any, Dict, Optional

try:  # pragma: no cover - import guard exercised indirectly via tests
    import memray  # type: ignore[import-not-found]

    MEMRAY_AVAILABLE = True
    _MEMRAY_IMPORT_ERROR: str | None = None
except Exception as exc:  # noqa: BLE001 - capture any import-time failure
    memray = None  # type: ignore[assignment]
    MEMRAY_AVAILABLE = False
    _MEMRAY_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

IS_WINDOWS = sys.platform.startswith("win") or platform.system() == "Windows"


class MemrayUnavailableError(RuntimeError):
    """Raised when memray is requested but cannot be used on this platform."""


class MemrayProfiler:
    """Process-wide controller for ``memray.Tracker``.

    Parameters
    ----------
    output_dir:
        Directory where ``.bin`` capture files are written.  Created on demand.
    """

    DEFAULT_OUTPUT_EXT = ".bin"

    def __init__(self, output_dir: str = "./mem-snapshots") -> None:
        self._output_dir = output_dir
        self._lock = threading.Lock()
        self._tracker: Any | None = None  # memray.Tracker
        self._tracker_cm: Any | None = None  # context manager handle
        self._current_output: str | None = None
        self._started_at: float | None = None
        self._session_id: str | None = None

    # ------------------------------------------------------------------
    # Capability / introspection
    # ------------------------------------------------------------------

    @staticmethod
    def is_available() -> Dict[str, Any]:
        """Return availability info: ``{available, platform_supported, reason}``."""
        if IS_WINDOWS:
            return {
                "available": False,
                "platform_supported": False,
                "reason": "memray does not support Windows",
            }
        if not MEMRAY_AVAILABLE:
            return {
                "available": False,
                "platform_supported": True,
                "reason": (
                    "memray is not installed. Install with: "
                    "pip install 'fastapi_profiler[memray]'"
                ),
                "import_error": _MEMRAY_IMPORT_ERROR,
            }
        return {"available": True, "platform_supported": True, "reason": None}

    def is_running(self) -> bool:
        with self._lock:
            return self._tracker is not None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        output_path: str | None = None,
        native: bool = False,
        follow_fork: bool = False,
        trace_python_allocators: bool = False,
    ) -> Dict[str, Any]:
        """Start a new memray tracking session.

        Parameters
        ----------
        output_path:
            Explicit ``.bin`` output path.  When omitted a unique file is
            created under ``output_dir``.
        native:
            Capture C/C++ stack frames in addition to Python frames.
        follow_fork:
            Continue tracking child processes spawned via ``fork()``.
        trace_python_allocators:
            Also track allocations made via Python's allocator domain.
        """
        self._require_available()

        with self._lock:
            if self._tracker is not None:
                raise RuntimeError(
                    "memray tracker already running; call stop() first"
                )

            os.makedirs(self._output_dir, exist_ok=True)
            session_id = self._make_session_id()
            resolved_output = output_path or os.path.join(
                self._output_dir, f"memray-{session_id}{self.DEFAULT_OUTPUT_EXT}"
            )

            # memray.Tracker is a context manager; we drive it manually so
            # that start/stop can be independent HTTP calls.
            tracker = memray.Tracker(  # type: ignore[union-attr]
                resolved_output,
                native_traces=native,
                follow_fork=follow_fork,
                trace_python_allocators=trace_python_allocators,
            )
            try:
                tracker.__enter__()
            except Exception:
                # Surface the failure but keep state clean.
                self._tracker = None
                self._tracker_cm = None
                self._current_output = None
                self._started_at = None
                self._session_id = None
                raise

            self._tracker = tracker
            self._tracker_cm = tracker
            self._current_output = resolved_output
            self._started_at = time.time()
            self._session_id = session_id

            return {
                "running": True,
                "session_id": session_id,
                "output_path": resolved_output,
                "native": native,
                "follow_fork": follow_fork,
                "trace_python_allocators": trace_python_allocators,
                "started_at": self._started_at,
                "message": "started",
            }

    def stop(self) -> Dict[str, Any]:
        """Stop the active tracking session and finalise the ``.bin`` file."""
        with self._lock:
            if self._tracker is None or self._tracker_cm is None:
                return {
                    "running": False,
                    "message": "not running",
                }

            output = self._current_output
            session_id = self._session_id
            started_at = self._started_at

            try:
                self._tracker_cm.__exit__(None, None, None)
            finally:
                self._tracker = None
                self._tracker_cm = None
                self._current_output = None
                self._started_at = None
                self._session_id = None

            duration = time.time() - started_at if started_at else 0.0
            file_size = (
                os.path.getsize(output)
                if output and os.path.isfile(output)
                else 0
            )
            return {
                "running": False,
                "session_id": session_id,
                "output_path": output,
                "duration_seconds": duration,
                "file_size_bytes": file_size,
                "message": "stopped",
                "hint": (
                    "Render with: "
                    f"memray flamegraph {output}"
                    if output
                    else None
                ),
            }

    def status(self) -> Dict[str, Any]:
        """Return current session state."""
        with self._lock:
            availability = self.is_available()
            running = self._tracker is not None
            out: Dict[str, Any] = {
                "available": availability["available"],
                "platform_supported": availability["platform_supported"],
                "running": running,
                "output_dir": self._output_dir,
            }
            if availability["reason"]:
                out["reason"] = availability["reason"]
            if running:
                out.update({
                    "session_id": self._session_id,
                    "output_path": self._current_output,
                    "started_at": self._started_at,
                    "running_seconds": (
                        time.time() - self._started_at
                        if self._started_at
                        else 0.0
                    ),
                })
            return out

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_session_id() -> str:
        return f"{int(time.time())}-{uuid.uuid4().hex[:8]}"

    def _require_available(self) -> None:
        info = self.is_available()
        if not info["available"]:
            raise MemrayUnavailableError(info["reason"] or "memray unavailable")
