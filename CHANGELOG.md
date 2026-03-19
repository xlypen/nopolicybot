# Changelog

All notable changes to `nopolicybot` are documented in this file.

## 2026-03-11

### Added
- Learning feedback loop with A/B-aware biasing and decision quality APIs.
- Predictive risk signals (churn/toxicity/virality) and optimization-aware recommendations.
- Production hardening stack: monitoring/alerts endpoints, audit trail, rate limiting, payload guards.
- Operational templates and staged deploy workflow for `staging` and `production`.
- Modern admin dashboard integrations for predictive overview, decision quality, learning summary, and inline feedback actions.

### Changed
- Switched `/admin` to modern dashboard by default.
- Preserved legacy admin UX via `/admin-legacy` and explicit `/admin?legacy=1` compatibility mode.
- Refreshed project status report to match real implementation state and test coverage.

### Fixed
- Stabilized graph UX for very-large payloads with renderer selection hints and non-blocking fallbacks.
- Resolved smoke-check import path issue for direct script execution.

### Verification
- `pytest -q` passing.
- `python scripts/smoke_checks.py` passing.
