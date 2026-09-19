from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import Counter, deque
from uuid import uuid4

from fastapi import Request

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,80}$")
logger = logging.getLogger("repayguard.access")


class Telemetry:
    def __init__(self) -> None:
        self.started_at = time.time()
        self._lock = threading.Lock()
        self._status = Counter()
        self._routes = Counter()
        self._durations: deque[float] = deque(maxlen=1000)

    def record(self, method: str, path: str, status_code: int, duration_ms: float) -> None:
        with self._lock:
            self._status[str(status_code)] += 1
            self._routes[f"{method} {path}"] += 1
            self._durations.append(duration_ms)

    def snapshot(self) -> dict:
        with self._lock:
            durations = sorted(self._durations)
            total = sum(self._status.values())
            p50 = durations[int((len(durations) - 1) * 0.50)] if durations else 0
            p95 = durations[int((len(durations) - 1) * 0.95)] if durations else 0
            return {
                "status": "ok",
                "uptime_seconds": round(time.time() - self.started_at, 3),
                "requests_total": total,
                "responses_by_status": dict(sorted(self._status.items())),
                "requests_by_route": dict(self._routes.most_common(50)),
                "latency_ms": {"p50": round(p50, 3), "p95": round(p95, 3)},
                "sample_size": len(durations),
            }


async def observe_request(request: Request, call_next):
    started = time.perf_counter()
    supplied = request.headers.get("X-Request-ID", "")
    request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else f"req-{uuid4().hex}"
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        request.app.state.telemetry.record(request.method, request.url.path, status_code, duration_ms)
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 3),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
