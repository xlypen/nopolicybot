"""Tests for personality comparison (P-6)."""

from services.personality.comparison import (
    ComparisonResult,
    PersonalityCluster,
    build_ocean_verbal_summary,
    cluster_community,
    compare_two,
)
from services.personality.schema import OceanTraits, PersonalityProfile


def test_compare_two_identical():
    p = PersonalityProfile(
        user_id="1",
        username="a",
        ocean=OceanTraits(openness=0.7, conscientiousness=0.5, extraversion=0.6, agreeableness=0.4, neuroticism=0.3),
    )
    r = compare_two(p, p)
    assert r.similarity_score == 1.0
    assert all(d == 0 for d in r.ocean_deltas.values())


def test_build_ocean_verbal_summary():
    deltas = {
        "openness": 0.1,
        "conscientiousness": -0.2,
        "extraversion": 0.0,
        "agreeableness": 0.05,
        "neuroticism": -0.15,
    }
    lines = build_ocean_verbal_summary("Paul", "Вильям", deltas, eps=0.02)
    assert len(lines) == 5
    assert any("Вильям" in x and "Paul" in x for x in lines)
    assert any("почти не различаются" in x for x in lines)  # extraversion ~ 0


def test_compare_two_different():
    pa = PersonalityProfile(
        user_id="1",
        username="a",
        ocean=OceanTraits(openness=0.2, conscientiousness=0.8, extraversion=0.3, agreeableness=0.9, neuroticism=0.2),
    )
    pb = PersonalityProfile(
        user_id="2",
        username="b",
        ocean=OceanTraits(openness=0.8, conscientiousness=0.2, extraversion=0.9, agreeableness=0.1, neuroticism=0.8),
    )
    r = compare_two(pa, pb)
    assert r.similarity_score < 0.5
    assert r.ocean_deltas["openness"] == 0.6
    assert len(r.most_different_dimensions) == 3
    assert len(r.most_similar_dimensions) == 3


def test_cluster_community_empty():
    assert cluster_community([]) == []


def test_cluster_community_single():
    assert cluster_community([(1, PersonalityProfile(user_id="1"))]) == []


def test_cluster_community_two():
    pa = PersonalityProfile(user_id="1", ocean=OceanTraits(openness=0.2, conscientiousness=0.5, extraversion=0.5, agreeableness=0.5, neuroticism=0.5))
    pb = PersonalityProfile(user_id="2", ocean=OceanTraits(openness=0.8, conscientiousness=0.5, extraversion=0.5, agreeableness=0.5, neuroticism=0.5))
    clusters = cluster_community([(1, pa), (2, pb)], n_clusters=2)
    assert len(clusters) == 2
    assert sum(c.size for c in clusters) == 2


def test_cluster_community_five():
    profiles = [
        (i, PersonalityProfile(user_id=str(i), ocean=OceanTraits(openness=0.5 + i * 0.01, conscientiousness=0.5, extraversion=0.5, agreeableness=0.5, neuroticism=0.5)))
        for i in range(1, 6)
    ]
    clusters = cluster_community(profiles, n_clusters=2)
    assert len(clusters) >= 1
    assert sum(c.size for c in clusters) == 5
