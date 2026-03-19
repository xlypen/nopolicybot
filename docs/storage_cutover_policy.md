# Storage Cutover Policy

This policy defines how the project moves from mixed JSON/DB operation to DB-first operation safely.

## Goals

- Keep bot behavior stable while reducing legacy JSON dependence.
- Ensure API and analytics consistency across storage layers.
- Make rollback explicit and low-risk.

## Current state

- JSON and DB paths coexist in selected surfaces.
- DB repositories and migration tooling are available.
- Storage status endpoints report parity signals (`/api/storage/status`, `/api/storage/cutover-report`).
- Runtime mode is controlled by `STORAGE_MODE` (`dual` / `db_first` / `db_only`).
- Parity monitor writes periodic diffs to `data/parity_diff.log` (default every 300s).

## Cutover phases

### Phase A - Observe parity (required)

- Compare JSON vs DB counts daily for users/messages/edges.
- Block progression if drift is unexplained.
- Require at least 7 consecutive days of stable parity for active chats.

### Phase B - DB-first reads (controlled)

- Enable DB-first read paths for admin analytics endpoints.
- Keep JSON fallback behind guarded feature switch.
- Monitor:
  - response latency delta,
  - error rate delta,
  - parity drift after read switch.

### Phase C - JSON write deprecation

- Stop JSON writes for entities that pass parity and stability gates.
- Keep one emergency rollback window (documented duration and owner).

### Phase D - JSON fallback removal

- Remove JSON fallback for already stable surfaces.
- Keep archival exports but stop runtime dependency.

## Exit criteria

- No critical parity drift for 14 consecutive days after DB-first switch.
- No increase in 5xx or alert severity attributable to storage layer.
- Recovery drill for DB path validated (backup + restore + smoke pass).

## Rollback policy

- Rollback is permitted only when:
  - critical data inconsistency appears, or
  - API reliability drops beyond SLO thresholds.
- Rollback actions:
  - re-enable JSON fallback switch,
  - capture incident context in audit log,
  - run smoke checks and parity diff immediately.
