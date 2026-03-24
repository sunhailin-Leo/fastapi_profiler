"""
Statistics collection module for fastapi_profiler.

This module provides data structures and classes to collect and aggregate
performance statistics for FastAPI routes.
"""

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Deque, List, Optional


@dataclass
class ProfileRecord:
    """
    Represents a single request profile record.
    
    Attributes:
        request_id: Unique identifier for the request (UUID4)
        path: Request path
        method: HTTP method
        status_code: HTTP status code
        duration_ms: Request duration in milliseconds
        timestamp: ISO 8601 formatted timestamp
        profile_output: Optional pyinstrument text output
    """
    request_id: str
    path: str
    method: str
    status_code: int
    duration_ms: float
    timestamp: str
    profile_output: Optional[str] = None
    
    @classmethod
    def create(
        cls,
        path: str,
        method: str,
        status_code: int,
        duration_ms: float,
        profile_output: Optional[str] = None
    ) -> "ProfileRecord":
        """Factory method to create a ProfileRecord with generated request_id and timestamp."""
        return cls(
            request_id=str(uuid.uuid4()),
            path=path,
            method=method,
            status_code=status_code,
            duration_ms=duration_ms,
            timestamp=datetime.utcnow().isoformat(),
            profile_output=profile_output
        )


@dataclass
class RouteStats:
    """
    Statistics for a specific route.
    
    Attributes:
        path: Request path
        method: HTTP method
        count: Total number of requests
        error_count: Number of error responses (4xx and 5xx)
        total_duration_ms: Total duration of all requests in milliseconds
        max_duration_ms: Maximum request duration
        min_duration_ms: Minimum request duration
        _samples: Internal deque storing recent duration samples (max 1000)
    """
    path: str
    method: str
    count: int = 0
    error_count: int = 0
    total_duration_ms: float = 0.0
    max_duration_ms: float = 0.0
    min_duration_ms: float = float('inf')
    _samples: Deque[float] = field(default_factory=lambda: deque(maxlen=1000))
    
    @property
    def avg_duration_ms(self) -> float:
        """Average duration in milliseconds."""
        return self.total_duration_ms / self.count if self.count > 0 else 0.0
    
    @property
    def p95_duration_ms(self) -> float:
        """
        95th percentile duration in milliseconds.
        
        Returns max_duration_ms if insufficient samples.
        """
        if len(self._samples) < 2:
            return self.max_duration_ms
        
        sorted_samples = sorted(self._samples)
        index = int(len(sorted_samples) * 0.95)
        return sorted_samples[min(index, len(sorted_samples) - 1)]
    
    @property
    def p99_duration_ms(self) -> float:
        """
        99th percentile duration in milliseconds.
        
        Returns max_duration_ms if insufficient samples.
        """
        if len(self._samples) < 2:
            return self.max_duration_ms
        
        sorted_samples = sorted(self._samples)
        index = int(len(sorted_samples) * 0.99)
        return sorted_samples[min(index, len(sorted_samples) - 1)]
    
    def record(self, duration_ms: float, status_code: int) -> None:
        """
        Record a new request for this route.
        
        Args:
            duration_ms: Request duration in milliseconds
            status_code: HTTP status code
        """
        self.count += 1
        self.total_duration_ms += duration_ms
        
        if status_code >= 400:
            self.error_count += 1
        
        self.max_duration_ms = max(self.max_duration_ms, duration_ms)
        self.min_duration_ms = min(self.min_duration_ms, duration_ms)
        self._samples.append(duration_ms)
    
    def to_dict(self) -> Dict:
        """
        Convert to serializable dictionary (excludes _samples).
        
        Returns:
            Dictionary representation of the stats
        """
        return {
            'path': self.path,
            'method': self.method,
            'count': self.count,
            'error_count': self.error_count,
            'total_duration_ms': self.total_duration_ms,
            'max_duration_ms': self.max_duration_ms,
            'min_duration_ms': self.min_duration_ms if self.min_duration_ms != float('inf') else 0.0,
            'avg_duration_ms': self.avg_duration_ms,
            'p95_duration_ms': self.p95_duration_ms,
            'p99_duration_ms': self.p99_duration_ms
        }


class StatsCollector:
    """
    Collector for route statistics and profile history.
    
    Thread-safe statistics collection using asyncio.Lock.
    """
    
    def __init__(self, max_profiles_per_route: int = 10):
        """
        Initialize the StatsCollector.
        
        Args:
            max_profiles_per_route: Maximum number of profile records to keep per route
        """
        self._route_stats: Dict[str, RouteStats] = {}
        self._route_history: Dict[str, Deque[ProfileRecord]] = {}
        self._max_profiles_per_route = max_profiles_per_route
        self._lock = asyncio.Lock()
    
    def _get_route_key(self, path: str, method: str) -> str:
        """Generate a unique key for a route."""
        return f"{method}:{path}"
    
    async def record(
        self,
        path: str,
        method: str,
        duration_ms: float,
        status_code: int,
        profile_output: Optional[str] = None
    ) -> None:
        """
        Record a request with its performance metrics.
        
        Args:
            path: Request path
            method: HTTP method
            duration_ms: Request duration in milliseconds
            status_code: HTTP status code
            profile_output: Optional profiling output
        """
        route_key = self._get_route_key(path, method)
        
        async with self._lock:
            # Update route statistics
            if route_key not in self._route_stats:
                self._route_stats[route_key] = RouteStats(path=path, method=method)
            self._route_stats[route_key].record(duration_ms, status_code)
            
            # Store profile record in history
            if route_key not in self._route_history:
                self._route_history[route_key] = deque(maxlen=self._max_profiles_per_route)
            
            profile_record = ProfileRecord.create(
                path=path,
                method=method,
                status_code=status_code,
                duration_ms=duration_ms,
                profile_output=profile_output
            )
            self._route_history[route_key].append(profile_record)
    
    async def get_all_stats(self) -> List[Dict]:
        """
        Get statistics for all routes, sorted by average duration descending.
        
        Returns:
            List of dictionaries containing route statistics
        """
        async with self._lock:
            stats_list = [stats.to_dict() for stats in self._route_stats.values()]
            # Sort by avg_duration_ms descending
            stats_list.sort(key=lambda x: x['avg_duration_ms'], reverse=True)
            return stats_list
    
    async def reset(self) -> None:
        """Clear all statistics and history."""
        async with self._lock:
            self._route_stats.clear()
            self._route_history.clear()
    
    async def get_route_history(
        self,
        path: str,
        method: str,
        limit: int = 10
    ) -> List[ProfileRecord]:
        """
        Get recent profile records for a specific route.
        
        Args:
            path: Request path
            method: HTTP method
            limit: Maximum number of records to return
            
        Returns:
            List of ProfileRecord objects, most recent first
        """
        route_key = self._get_route_key(path, method)
        
        async with self._lock:
            if route_key not in self._route_history:
                return []
            
            history = list(self._route_history[route_key])
            history.reverse()  # Most recent first
            return history[:limit]
