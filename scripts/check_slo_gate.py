from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path


THRESHOLD_KEYS = {
    "p95_latency_ms_max",
    "error_5xx_rate_max",
    "rate_limit_hit_ratio_max",
    "alert_volume_per_hour_max",
}


def _load_thresholds(path: Path) -> dict[str, float | None]:
    text = path.read_text(encoding="utf-8")
    out: dict[str, float | None] = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([a-z0-9_]+)\s*:\s*(.+?)\s*$", line.strip())
        if not m:
            continue
        key = str(m.group(1) or "").strip()
        raw = str(m.group(2) or "").strip().strip("'\"")
        if key not in THRESHOLD_KEYS:
            continue
        if raw.lower() in {"null", "none", "tbd", "na", "n/a"}:
            out[key] = None
            continue
        out[key] = float(raw)
    missing = [k for k in THRESHOLD_KEYS if k not in out]
    if missing:
        raise RuntimeError(f"SLO thresholds missing keys: {', '.join(sorted(missing))}")
    return out


def _fetch_url(url: str, auth_token: str) -> str:
    req = urllib.request.Request(str(url).strip())
    req.add_header("Accept", "application/json, text/plain;q=0.9,*/*;q=0.5")
    if auth_token:
        req.add_header("Authorization", f"Bearer {auth_token}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_prom_text(raw: str) -> dict:
    samples: dict[str, float] = {}
    for line in str(raw or "").splitlines():
        src = line.strip()
        if not src or src.startswith("#"):
            continue
        parts = src.split()
        if len(parts) != 2:
            continue
        metric, value = parts[0], parts[1]
        try:
            samples[str(metric)] = float(value)
        except Exception:
            continue

    req = 0.0
    err5 = 0.0
    p95 = 0.0
    for metric, value in samples.items():
        if metric.endswith("_requests_total"):
            req = float(value)
        elif metric.endswith("_errors_5xx_total"):
            err5 = float(value)
        elif metric.endswith("_latency_p95_ms"):
            p95 = float(value)
    rate_5xx = (err5 / req) if req > 0 else 0.0
    return {
        "requests_total": int(req),
        "latency_ms": {"p95": float(p95)},
        "errors": {"5xx_total": int(err5), "5xx_rate": float(rate_5xx)},
        "status_counts": {},
    }


def _load_remote_metrics(metrics_url: str, auth_token: str) -> dict:
    raw = _fetch_url(metrics_url, auth_token=auth_token)
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict) and isinstance(payload.get("metrics"), dict):
            return payload["metrics"]
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return _parse_prom_text(raw)


def _load_local_metrics() -> dict:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.environ.setdefault("ADMIN_TOKEN", "local-slo-gate-token-1234567890")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/bot.db")
    from fastapi.testclient import TestClient

    from api.main import app

    token = os.getenv("ADMIN_TOKEN", "")
    with TestClient(app) as client:
        resp = client.get("/api/v2/metrics", headers={"Authorization": f"Bearer {token}"})
        if resp.status_code not in (200,):
            raise RuntimeError(f"Failed to read local metrics endpoint, status={resp.status_code}")
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if isinstance(body, dict) and isinstance(body.get("metrics"), dict):
            return body["metrics"]
        if isinstance(body, dict):
            return body
    raise RuntimeError("Unable to load local metrics")


def _extract_values(metrics: dict, alert_volume_per_hour: float | None = None) -> dict[str, float | None]:
    req = int(metrics.get("requests_total", 0) or 0)
    p95 = float((metrics.get("latency_ms") or {}).get("p95", 0.0) or 0.0)

    errors = metrics.get("errors") or {}
    rate_5xx = errors.get("5xx_rate")
    if rate_5xx is None:
        status_counts = metrics.get("status_counts") or {}
        err5 = sum(int(v) for k, v in status_counts.items() if str(k).startswith("5"))
        rate_5xx = float(err5) / float(max(1, req))

    ratio_rl = metrics.get("rate_limit_hit_ratio")
    if ratio_rl is None:
        status_counts = metrics.get("status_counts") or {}
        hits_429 = int(status_counts.get("429", 0) or 0)
        ratio_rl = float(hits_429) / float(max(1, req))

    return {
        "p95_latency_ms_max": float(p95),
        "error_5xx_rate_max": float(rate_5xx),
        "rate_limit_hit_ratio_max": float(ratio_rl),
        "alert_volume_per_hour_max": alert_volume_per_hour,
    }


def _check_slo(thresholds: dict[str, float | None], values: dict[str, float | None]) -> tuple[list[str], list[str]]:
    violations: list[str] = []
    checks: list[str] = []
    for key in sorted(THRESHOLD_KEYS):
        limit = thresholds.get(key)
        current = values.get(key)
        if limit is None:
            checks.append(f"{key}: skipped (threshold is null)")
            continue
        if current is None:
            violations.append(f"{key}: missing metric value (threshold={limit})")
            continue
        if float(current) <= float(limit):
            checks.append(f"{key}: ok ({current:.6f} <= {limit:.6f})")
        else:
            violations.append(f"{key}: violated ({current:.6f} > {limit:.6f})")
    return checks, violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate runtime metrics against docs/slo.md thresholds.")
    parser.add_argument("--slo-file", default="docs/slo.md")
    parser.add_argument("--metrics-url", default="")
    parser.add_argument("--alerts-url", default="")
    parser.add_argument("--auth-token", default="")
    parser.add_argument("--require-remote", action="store_true")
    parser.add_argument("--metrics-json-file", default="")
    args = parser.parse_args()

    thresholds = _load_thresholds(Path(args.slo_file))
    auth_token = str(args.auth_token or os.getenv("SLO_AUTH_TOKEN", "")).strip()

    if args.metrics_json_file:
        payload = json.loads(Path(args.metrics_json_file).read_text(encoding="utf-8"))
        metrics = payload["metrics"] if isinstance(payload, dict) and isinstance(payload.get("metrics"), dict) else payload
    elif args.metrics_url:
        metrics = _load_remote_metrics(args.metrics_url, auth_token=auth_token)
    else:
        if args.require_remote:
            raise RuntimeError("--require-remote is set but --metrics-url is empty")
        metrics = _load_local_metrics()

    alert_volume = None
    if args.alerts_url:
        # Placeholder for future alert volume parsing once threshold is set in docs/slo.md.
        _ = _fetch_url(args.alerts_url, auth_token=auth_token)
        alert_volume = None

    values = _extract_values(metrics, alert_volume_per_hour=alert_volume)
    checks, violations = _check_slo(thresholds, values)

    print("[slo-gate] thresholds:", json.dumps(thresholds, ensure_ascii=False, sort_keys=True))
    print("[slo-gate] values:", json.dumps(values, ensure_ascii=False, sort_keys=True))
    for row in checks:
        print(f"[slo-gate] {row}")
    for row in violations:
        print(f"[slo-gate] FAIL: {row}")

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
