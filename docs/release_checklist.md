# Release Checklist

This checklist is used for promoting changes from `restore-from-archive` to production.

## 1. Scope freeze

- Confirm target branch and commit SHA for release.
- Ensure working tree is clean and all release docs are updated (`CHANGELOG.md`, `PROJECT_STATUS_REPORT.md`).
- Confirm no pending schema/data migration surprises.

## 2. Pre-release validation

- Run:
  - `./scripts/pre_release_check.sh`
- Verify:
  - full test suite passes,
  - smoke checks pass,
  - deploy script dry-run works for both `staging` and `production`.

## 3. Staging rollout

- Trigger `Deploy Pipeline` workflow with `target=staging`.
- Validate:
  - `/health`,
  - `/api/v2/health`,
  - `/api/monitoring/metrics`,
  - `/api/v2/metrics`.
- Observe for 24-48h:
  - p95 latency,
  - 5xx ratio,
  - rate-limit hit ratio,
  - audit spike alerts.

## 4. Production readiness gate

- Confirm staging metrics are stable against SLO thresholds:
  - p95 latency: <= 800ms on main API surfaces,
  - 5xx ratio: <= 1% rolling,
  - no persistent critical alerts.
- Confirm rollback plan and on-call ownership.

## 5. Production rollout

- Trigger `Deploy Pipeline` workflow with `target=production`.
- Perform post-deploy checks:
  - health endpoints,
  - key admin API paths,
  - realtime activity signal,
  - dashboard graph load.

## 6. Post-release closure

- Record final release SHA and date in `CHANGELOG.md`.
- Attach smoke/test artifacts to release notes.
- Open follow-up tickets for non-blocking tails (if any).
