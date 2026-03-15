"""Behavioral verification of personality profile (P-9)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from services.personality.schema import PersonalityProfile

logger = logging.getLogger(__name__)

CONFLICT_KEYWORDS = ("спор", "конфликт", "срач", "агресс", "руг", "резк", "груб", "хам", "обвинен")


@dataclass
class BehavioralSignals:
    """Observable behavior derived from messages and metrics."""

    message_count: int = 0
    conflict_ratio: float = 0.0  # % of messages with conflict keywords
    avg_message_length: float = 0.0
    engagement_score: float = 0.0
    sentiment_negative_ratio: float = 0.0


@dataclass
class VerificationResult:
    """Result of profile vs behavior verification."""

    correlation_score: float = 0.0  # 0–1, how well profile matches behavior
    reliability_badge: str = "low"  # high | medium | low
    matched_dimensions: list[str] = None
    mismatched_dimensions: list[str] = None

    def __post_init__(self):
        if self.matched_dimensions is None:
            self.matched_dimensions = []
        if self.mismatched_dimensions is None:
            self.mismatched_dimensions = []


def compute_behavioral_signals(
    messages: list[dict],
    metrics: dict | None = None,
) -> BehavioralSignals:
    """Extract observable behavioral signals from messages and optional metrics."""
    metrics = metrics or {}
    n = len(messages)
    if n == 0:
        return BehavioralSignals()

    conflict_count = 0
    total_len = 0
    for m in messages:
        t = (m.get("text") or "").lower()
        if any(k in t for k in CONFLICT_KEYWORDS):
            conflict_count += 1
        total_len += len(t)

    engagement = float(metrics.get("engagement_score", 0.0) or 0.0)
    neg_ratio = 0.0
    totals = metrics.get("totals") or {}
    total_m = int(totals.get("messages", 0) or metrics.get("total_messages", 0) or 1)
    if total_m > 0:
        neg = int(totals.get("negative", 0) or metrics.get("negative_sentiment", 0) or 0)
        neg_ratio = neg / total_m

    return BehavioralSignals(
        message_count=n,
        conflict_ratio=conflict_count / n if n else 0.0,
        avg_message_length=total_len / n if n else 0.0,
        engagement_score=engagement,
        sentiment_negative_ratio=neg_ratio,
    )


def verify_profile(
    profile: PersonalityProfile,
    behavior: BehavioralSignals,
) -> VerificationResult:
    """
    Correlate profile predictions with observed behavior.
    Returns correlation score and reliability badge.
    """
    matched: list[str] = []
    mismatched: list[str] = []
    scores: list[float] = []

    # conflict_tendency vs conflict_ratio
    pred_conflict = profile.communication.conflict_tendency
    if behavior.message_count >= 10:
        diff = abs(pred_conflict - behavior.conflict_ratio)
        if diff < 0.25:
            matched.append("conflict_tendency")
            scores.append(1.0 - diff)
        else:
            mismatched.append("conflict_tendency")
            scores.append(max(0.0, 1.0 - diff))

    # extraversion vs avg_message_length (rough: longer messages -> more extraverted)
    pred_extra = profile.ocean.extraversion
    if behavior.avg_message_length > 0:
        norm_len = min(1.0, behavior.avg_message_length / 200.0)
        diff = abs(pred_extra - norm_len)
        if diff < 0.3:
            matched.append("extraversion")
        scores.append(max(0.0, 1.0 - diff))

    # agreeableness vs conflict_ratio (negative correlation)
    pred_agree = profile.ocean.agreeableness
    if behavior.message_count >= 10:
        expected_agree = 1.0 - behavior.conflict_ratio
        diff = abs(pred_agree - expected_agree)
        if diff < 0.3:
            matched.append("agreeableness")
        scores.append(max(0.0, 1.0 - diff))

    if not scores:
        return VerificationResult(
            correlation_score=0.5,
            reliability_badge="low",
            matched_dimensions=[],
            mismatched_dimensions=[],
        )

    correlation = sum(scores) / len(scores)
    if correlation >= 0.75:
        badge = "high"
    elif correlation >= 0.5:
        badge = "medium"
    else:
        badge = "low"

    return VerificationResult(
        correlation_score=round(correlation, 3),
        reliability_badge=badge,
        matched_dimensions=matched,
        mismatched_dimensions=mismatched,
    )
