from __future__ import annotations

import re
import threading
import time
from collections import Counter, deque

_LOCK = threading.Lock()
_STATE: dict[str, dict] = {}
_ID_RE = re.compile(r"/-?\d+")


def _norm_path(path: str) -> str:
    src = str(path or "/")
    src = _ID_RE.sub("/:id", src)
    return src[:240]


def _ensure(app_name: str) -> dict:
    key = str(app_name or "default")
    with _LOCK:
        payload = _STATE.get(key)
        if isinstance(payload, dict):
            return payload
        payload = {
            "started_at": time.time(),
            "requests_total": 0,
            "status_counts": Counter(),
            "path_counts": Counter(),
            "method_counts": Counter(),
            "durations_ms": deque(maxlen=4000),
            "last_error_at": "",
        }
        _STATE[key] = payload
        return payload


def record_request(app_name: str, method: str, path: str, status_code: int, duration_ms: float) -> None:
    state = _ensure(app_name)
    m = str(method or "GET").upper()
    p = _norm_path(path)
    s = int(status_code or 0)
    d = max(0.0, float(duration_ms or 0.0))
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _LOCK:
        state["requests_total"] += 1
        state["method_counts"][m] += 1
        state["path_counts"][p] += 1
        state["status_counts"][str(s)] += 1
        state["durations_ms"].append(d)
        if s >= 500:
            state["last_error_at"] = now


def snapshot(app_name: str) -> dict:
    state = _ensure(app_name)
    with _LOCK:
        durations = list(state["durations_ms"])
        req_total = int(state["requests_total"])
        status_counts = dict(state["status_counts"])
        method_counts = dict(state["method_counts"])
        path_counts = dict(state["path_counts"])
        started_at = float(state["started_at"])
        last_error_at = str(state.get("last_error_at") or "")
    durations.sort()
    avg = (sum(durations) / len(durations)) if durations else 0.0
    p95 = durations[int(len(durations) * 0.95) - 1] if durations else 0.0
    err5 = sum(int(v) for k, v in status_counts.items() if str(k).startswith("5"))
    err4 = sum(int(v) for k, v in status_counts.items() if str(k).startswith("4"))
    return {
        "app": str(app_name),
        "started_at": started_at,
        "uptime_sec": max(0, int(time.time() - started_at)),
        "requests_total": req_total,
        "status_counts": status_counts,
        "method_counts": method_counts,
        "top_paths": sorted(path_counts.items(), key=lambda x: x[1], reverse=True)[:20],
        "latency_ms": {"avg": round(float(avg), 3), "p95": round(float(p95), 3), "samples": len(durations)},
        "errors": {
            "4xx_total": int(err4),
            "5xx_total": int(err5),
            "5xx_rate": round(float(err5) / float(max(1, req_total)), 4),
            "last_error_at": last_error_at,
        },
    }


def build_alerts(metrics_snapshot: dict, recent_audit_events: list[dict] | None = None) -> list[dict]:
    snap = metrics_snapshot if isinstance(metrics_snapshot, dict) else {}
    errors = snap.get("errors") or {}
    latency = snap.get("latency_ms") or {}
    alerts: list[dict] = []

    if float(errors.get("5xx_rate", 0.0) or 0.0) >= 0.05 and int(snap.get("requests_total", 0) or 0) >= 20:
        alerts.append(
            {
                "level": "warning",
                "type": "high_5xx_rate",
                "message": f"5xx rate is high: {errors.get('5xx_rate')}",
            }
        )
    if float(latency.get("p95", 0.0) or 0.0) >= 1500.0 and int(latency.get("samples", 0) or 0) >= 20:
        alerts.append(
            {
                "level": "warning",
                "type": "high_latency_p95",
                "message": f"Latency p95 is high: {latency.get('p95')} ms",
            }
        )
    if isinstance(recent_audit_events, list):
        sec_warnings = [
            x
            for x in recent_audit_events
            if isinstance(x, dict) and str(x.get("severity", "")).lower() in {"warning", "error"}
        ]
        if len(sec_warnings) >= 5:
            alerts.append(
                {
                    "level": "warning",
                    "type": "security_audit_spike",
                    "message": f"Recent audit warnings/errors: {len(sec_warnings)}",
                }
            )
    return alerts


def to_prometheus_text(metrics_snapshot: dict, prefix: str = "nopolicybot") -> str:
    snap = metrics_snapshot if isinstance(metrics_snapshot, dict) else {}
    req_total = int(snap.get("requests_total", 0) or 0)
    up = int(max(0, int(snap.get("uptime_sec", 0) or 0)))
    latency = snap.get("latency_ms") or {}
    errors = snap.get("errors") or {}
    lines = [
        f"# HELP {prefix}_requests_total Total processed requests",
        f"# TYPE {prefix}_requests_total counter",
        f"{prefix}_requests_total {req_total}",
        f"# HELP {prefix}_uptime_seconds App uptime in seconds",
        f"# TYPE {prefix}_uptime_seconds gauge",
        f"{prefix}_uptime_seconds {up}",
        f"# HELP {prefix}_latency_p95_ms Rolling p95 latency in milliseconds",
        f"# TYPE {prefix}_latency_p95_ms gauge",
        f"{prefix}_latency_p95_ms {float(latency.get('p95', 0.0) or 0.0)}",
        f"# HELP {prefix}_errors_5xx_total Total 5xx responses",
        f"# TYPE {prefix}_errors_5xx_total counter",
        f"{prefix}_errors_5xx_total {int(errors.get('5xx_total', 0) or 0)}",
    ]
    return "\n".join(lines) + "\n"
