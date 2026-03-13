# Production Hardening Runbook

## Monitoring

- FastAPI metrics endpoint: `GET /api/v2/metrics` (`format=prom` for Prometheus text).
- FastAPI alerts endpoint: `GET /api/v2/alerts`.
- Flask admin metrics endpoint: `GET /api/monitoring/metrics` (`format=prom` supported).
- Flask admin alerts endpoint: `GET /api/monitoring/alerts`.

Both app surfaces now publish:
- request totals and status distribution,
- rolling latency (avg, p95),
- top endpoint paths,
- derived alert candidates (high 5xx rate, high p95, audit spikes).

## Audit Trail

- Security and runtime events are written to `data/audit_events.jsonl`.
- Examples: auth failures, rate-limit hits, blocked oversized payloads, 5xx responses.
- Alerts endpoints include latest audit excerpts for triage.

## Security Hardening

### FastAPI guards (`/api/v2/*`)

- IP-based fixed-window rate limit (default: `240 req/min`).
- URL size limit and payload size limit checks.
- Rejections produce:
  - `429` for throttling,
  - `414` for too-long URL,
  - `413` for oversized payload.

Env vars:
- `API_RATE_LIMIT_PER_MIN`
- `API_MAX_URL_LENGTH`
- `API_MAX_BODY_BYTES`

### Flask guards (`/api/*`)

- IP-based fixed-window rate limit (default: `300 req/min`).
- URL and payload size checks with the same response codes as above.

Env vars:
- `FLASK_RATE_LIMIT_PER_MIN`
- `FLASK_MAX_URL_LENGTH`
- `FLASK_MAX_BODY_BYTES`

## Logging

- Structured logging can be enabled with `LOG_JSON=1`.
- Default level: `INFO`; configurable via `LOG_LEVEL`.

## CI/CD

- `ci.yml` now runs:
  - static compile checks,
  - full test suite,
  - smoke checks.
- `deploy.yml` provides manual `staging|production` preflight + environment-gated deploy stage.
- deployment script: `scripts/deploy_release.sh`
  - supports real deploy via SSH secrets (`DEPLOY_HOST`, `DEPLOY_SSH_KEY`, etc.),
  - falls back to dry-run in CI when secrets are not configured.

## Ops Templates

Prepared templates are included in `deploy-ubuntu/`:

- `nginx-nopolicybot.conf` — reverse proxy layout for Flask + FastAPI,
- `logrotate-nopolicybot.conf` — log rotation policy,
- `prometheus-nopolicybot.yml` — scrape config for both metrics endpoints.