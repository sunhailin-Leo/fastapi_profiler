"""tracemalloc-based memory profiler.

A thin, thread-safe wrapper around the standard-library :mod:`tracemalloc`
module.  Designed to be driven by HTTP control endpoints exposed by
:class:`fastapi_profiler.memory_middleware.MemoryProfilerMiddleware`.

Lifecycle
---------
1. ``start(frames=...)`` — calls :func:`tracemalloc.start` and remembers the
   configured frame depth.  Idempotent: re-calling ``start`` is a no-op.
2. ``snapshot()`` — captures a :class:`tracemalloc.Snapshot`, persists it to
   ``snapshot_dir`` via :py:meth:`tracemalloc.Snapshot.dump` and returns a
   metadata dict (path, top-N statistics, traced memory).
3. ``compare(latest=True)`` — diffs the two most recent snapshots and
   returns the top growers.
4. ``stop()`` — calls :func:`tracemalloc.stop` and clears in-memory snapshot
   state (files on disk are kept).

Snapshot files use the ``.snap`` extension and are written via the official
:py:meth:`tracemalloc.Snapshot.dump` API which is stable across CPython
versions.
"""

from __future__ import annotations

import os
import threading
import time
import tracemalloc
import uuid
from typing import Any, Dict, List, Optional, Tuple


class TracemallocProfiler:
    """Thread-safe controller for ``tracemalloc``.

    Parameters
    ----------
    snapshot_dir:
        Directory where ``.snap`` files are written.  Created on demand.
    default_frames:
        Frame depth to use when ``start()`` is called without an explicit
        ``frames`` argument.  Higher values give richer tracebacks but
        increase overhead and memory usage.
    default_top:
        Default number of top allocation entries to return from
        ``snapshot()`` / ``compare()``.
    """

    DEFAULT_FRAMES = 25
    DEFAULT_TOP = 20
    SNAPSHOT_EXT = ".snap"

    def __init__(
        self,
        snapshot_dir: str = "./mem-snapshots",
        default_frames: int = DEFAULT_FRAMES,
        default_top: int = DEFAULT_TOP,
    ) -> None:
        self._snapshot_dir = snapshot_dir
        self._default_frames = default_frames
        self._default_top = default_top

        self._lock = threading.Lock()
        # Ordered list of (snapshot_id, file_path, snapshot) tuples.
        # Keeping the in-memory Snapshot avoids a re-load round-trip for
        # the common "compare last two" use case.
        self._snapshots: List[Tuple[str, str, tracemalloc.Snapshot]] = []
        self._frames: int = default_frames

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        """Return True when ``tracemalloc`` is currently tracing."""
        return tracemalloc.is_tracing()

    def start(self, frames: int | None = None) -> Dict[str, Any]:
        """Start ``tracemalloc`` if not already running.

        Returns a status dict.  Calling ``start`` while already running is a
        no-op and the existing frame depth is preserved.
        """
        with self._lock:
            if tracemalloc.is_tracing():
                return self._status_locked(message="already running")

            self._frames = int(frames if frames is not None else self._default_frames)
            if self._frames < 1:
                raise ValueError("frames must be >= 1")
            tracemalloc.start(self._frames)
            return self._status_locked(message="started")

    def stop(self) -> Dict[str, Any]:
        """Stop ``tracemalloc`` and clear in-memory snapshot state."""
        with self._lock:
            was_running = tracemalloc.is_tracing()
            if was_running:
                tracemalloc.stop()
            self._snapshots.clear()
            return {
                "running": False,
                "message": "stopped" if was_running else "not running",
                "frames": self._frames,
                "snapshot_count": 0,
            }

    def status(self) -> Dict[str, Any]:
        """Return current status (running flag, traced memory, snapshot count)."""
        with self._lock:
            return self._status_locked()

    # ------------------------------------------------------------------
    # Snapshot operations
    # ------------------------------------------------------------------

    def snapshot(self, top: int | None = None) -> Dict[str, Any]:
        """Capture a snapshot, persist to disk, return metadata + top stats."""
        if not tracemalloc.is_tracing():
            raise RuntimeError(
                "tracemalloc is not running; call start() before snapshot()"
            )

        top_n = int(top if top is not None else self._default_top)
        if top_n < 1:
            raise ValueError("top must be >= 1")

        snap = tracemalloc.take_snapshot()
        # Filter out tracemalloc's own frames so users only see their code.
        snap = snap.filter_traces((
            tracemalloc.Filter(False, tracemalloc.__file__),
            tracemalloc.Filter(False, __file__),
        ))

        os.makedirs(self._snapshot_dir, exist_ok=True)
        snapshot_id = self._make_snapshot_id()
        file_path = os.path.join(self._snapshot_dir, snapshot_id + self.SNAPSHOT_EXT)
        snap.dump(file_path)

        current, peak = tracemalloc.get_traced_memory()

        with self._lock:
            self._snapshots.append((snapshot_id, file_path, snap))

        return {
            "snapshot_id": snapshot_id,
            "file_path": file_path,
            "traced_memory_current_bytes": current,
            "traced_memory_peak_bytes": peak,
            "top": _format_statistics(snap.statistics("lineno"), top_n),
        }

    def compare(
        self,
        top: int | None = None,
        snapshot_a: str | None = None,
        snapshot_b: str | None = None,
    ) -> Dict[str, Any]:
        """Diff two snapshots and return the top growers.

        By default compares the two most recent snapshots.  If
        ``snapshot_a`` / ``snapshot_b`` are provided they must reference
        snapshot ids previously returned by :py:meth:`snapshot`.
        """
        top_n = int(top if top is not None else self._default_top)
        if top_n < 1:
            raise ValueError("top must be >= 1")

        with self._lock:
            snap_a, snap_b, id_a, id_b = self._resolve_pair_locked(
                snapshot_a, snapshot_b
            )

        diffs = snap_b.compare_to(snap_a, "lineno")
        return {
            "snapshot_a": id_a,
            "snapshot_b": id_b,
            "top": _format_statistics(diffs, top_n, is_diff=True),
        }

    def list_snapshots(self) -> List[Dict[str, Any]]:
        """Return metadata for all snapshots captured in the current session."""
        with self._lock:
            return [
                {"snapshot_id": sid, "file_path": fpath}
                for sid, fpath, _ in self._snapshots
            ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _status_locked(self, message: str | None = None) -> Dict[str, Any]:
        running = tracemalloc.is_tracing()
        current = peak = 0
        if running:
            current, peak = tracemalloc.get_traced_memory()
        out: Dict[str, Any] = {
            "running": running,
            "frames": self._frames,
            "snapshot_count": len(self._snapshots),
            "snapshot_dir": self._snapshot_dir,
            "traced_memory_current_bytes": current,
            "traced_memory_peak_bytes": peak,
        }
        if message is not None:
            out["message"] = message
        return out

    def _resolve_pair_locked(
        self,
        snapshot_a: str | None,
        snapshot_b: str | None,
    ) -> Tuple[tracemalloc.Snapshot, tracemalloc.Snapshot, str, str]:
        if snapshot_a is None and snapshot_b is None:
            if len(self._snapshots) < 2:
                raise RuntimeError(
                    "need at least two snapshots to compare; "
                    "call snapshot() at least twice first"
                )
            id_a, _, snap_a = self._snapshots[-2]
            id_b, _, snap_b = self._snapshots[-1]
            return snap_a, snap_b, id_a, id_b

        if snapshot_a is None or snapshot_b is None:
            raise ValueError(
                "snapshot_a and snapshot_b must both be provided or both omitted"
            )

        index_by_id = {sid: (sid, snap) for sid, _, snap in self._snapshots}
        if snapshot_a not in index_by_id or snapshot_b not in index_by_id:
            raise KeyError("unknown snapshot_id")
        id_a, snap_a = index_by_id[snapshot_a]
        id_b, snap_b = index_by_id[snapshot_b]
        return snap_a, snap_b, id_a, id_b

    @staticmethod
    def _make_snapshot_id() -> str:
        # Sortable + unique: <unix_seconds>-<short uuid>
        return f"{int(time.time())}-{uuid.uuid4().hex[:8]}"


def _format_statistics(
    stats: List[Any],
    top_n: int,
    is_diff: bool = False,
) -> List[Dict[str, Any]]:
    """Convert tracemalloc.Statistic / StatisticDiff list into JSON-friendly dicts."""
    out: List[Dict[str, Any]] = []
    for stat in stats[:top_n]:
        frame = stat.traceback[0] if stat.traceback else None
        item: Dict[str, Any] = {
            "size_bytes": int(stat.size),
            "count": int(stat.count),
            "file": frame.filename if frame else "<unknown>",
            "line": int(frame.lineno) if frame else 0,
            "traceback": [
                f"{f.filename}:{f.lineno}" for f in stat.traceback
            ],
        }
        if is_diff:
            # StatisticDiff exposes size_diff / count_diff in addition.
            item["size_diff_bytes"] = int(getattr(stat, "size_diff", 0))
            item["count_diff"] = int(getattr(stat, "count_diff", 0))
        out.append(item)
    return out
