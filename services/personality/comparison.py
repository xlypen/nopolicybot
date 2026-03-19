"""Personality comparison and community clustering (P-6)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from services.personality.schema import OCEAN_KEYS, PersonalityProfile

logger = logging.getLogger(__name__)


@dataclass
class ComparisonResult:
    """Result of comparing two personality profiles."""

    user_id_a: str
    user_id_b: str
    username_a: str = ""
    username_b: str = ""
    ocean_deltas: dict[str, float] = None
    similarity_score: float = 0.0  # 0=opposite, 1=identical
    most_similar_dimensions: list[str] = None
    most_different_dimensions: list[str] = None

    def __post_init__(self):
        if self.ocean_deltas is None:
            self.ocean_deltas = {}
        if self.most_similar_dimensions is None:
            self.most_similar_dimensions = []
        if self.most_different_dimensions is None:
            self.most_different_dimensions = []


def _ocean_vector(profile: PersonalityProfile) -> list[float]:
    """Extract OCEAN values as vector."""
    return [getattr(profile.ocean, k) for k in OCEAN_KEYS]


def compare_two(profile_a: PersonalityProfile, profile_b: PersonalityProfile) -> ComparisonResult:
    """
    Compare two personality profiles.
    Returns deltas (b - a), similarity (1 - mean_abs_delta), and dimension rankings.
    """
    deltas: dict[str, float] = {}
    for k in OCEAN_KEYS:
        va = getattr(profile_a.ocean, k)
        vb = getattr(profile_b.ocean, k)
        deltas[k] = round(vb - va, 3)

    mean_abs = sum(abs(d) for d in deltas.values()) / len(OCEAN_KEYS) if OCEAN_KEYS else 0.0
    similarity = max(0.0, min(1.0, 1.0 - mean_abs))

    sorted_by_abs = sorted(OCEAN_KEYS, key=lambda x: abs(deltas[x]), reverse=True)
    most_different = sorted_by_abs[:3]
    most_similar = sorted_by_abs[-3:][::-1]

    return ComparisonResult(
        user_id_a=profile_a.user_id or "",
        user_id_b=profile_b.user_id or "",
        username_a=profile_a.username or "",
        username_b=profile_b.username or "",
        ocean_deltas=deltas,
        similarity_score=round(similarity, 3),
        most_similar_dimensions=most_similar,
        most_different_dimensions=most_different,
    )


@dataclass
class PersonalityCluster:
    """Cluster of users with similar personality profiles."""

    cluster_id: int
    user_ids: list[int]
    usernames: dict[int, str]
    centroid_ocean: dict[str, float]
    size: int


def _euclidean(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def cluster_community(
    profiles: list[tuple[int, PersonalityProfile]],
    n_clusters: int | None = None,
    max_clusters: int = 10,
) -> list[PersonalityCluster]:
    """
    Cluster users by OCEAN similarity (simple k-means).
    If n_clusters is None, use min(max_clusters, ceil(sqrt(len))).
    """
    if len(profiles) < 2:
        return []

    vectors = [(uid, _ocean_vector(p)) for uid, p in profiles]
    n = len(vectors)

    if n_clusters is None:
        import math
        n_clusters = min(max_clusters, max(2, int(math.ceil(math.sqrt(n)))))

    n_clusters = min(n_clusters, n)

    # Initialize centroids with first n_clusters points
    centroids = [vec[:] for _, vec in vectors[:n_clusters]]
    if len(centroids) < n_clusters:
        n_clusters = len(centroids)

    # Simple k-means iterations
    for _ in range(20):
        assignments: list[list[int]] = [[] for _ in range(n_clusters)]
        for uid, vec in vectors:
            best = min(range(n_clusters), key=lambda i: _euclidean(vec, centroids[i]))
            assignments[best].append(uid)

        # Update centroids
        for i in range(n_clusters):
            if not assignments[i]:
                continue
            member_vecs = [vec for uid, vec in vectors if uid in assignments[i]]
            dims = len(OCEAN_KEYS)
            new_centroid = [
                sum(v[j] for v in member_vecs) / len(member_vecs)
                for j in range(dims)
            ]
            centroids[i] = new_centroid

    # Build result
    result: list[PersonalityCluster] = []
    uid_to_profile = {uid: p for uid, p in profiles}
    for i, member_uids in enumerate(assignments):
        if not member_uids:
            continue
        member_profiles = [uid_to_profile[uid] for uid in member_uids]
        centroid = {
            k: round(
                sum(getattr(p.ocean, k) for p in member_profiles) / len(member_profiles),
                3,
            )
            for k in OCEAN_KEYS
        }
        usernames = {uid: (uid_to_profile.get(uid) or PersonalityProfile()).username or str(uid) for uid in member_uids}
        result.append(
            PersonalityCluster(
                cluster_id=i,
                user_ids=member_uids,
                usernames=usernames,
                centroid_ocean=centroid,
                size=len(member_uids),
            )
        )

    return result
