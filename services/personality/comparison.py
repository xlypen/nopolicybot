"""Personality comparison and community clustering (P-6)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from services.personality.schema import OCEAN_KEYS, PersonalityProfile

logger = logging.getLogger(__name__)

# Подписи для словесного сравнения (Δ = B − A): кто «выше» по шкале — тот сильнее проявляет черту.
OCEAN_VERBAL_RU: dict[str, str] = {
    "openness": "открытость к новому опыту",
    "conscientiousness": "добросовестность и организованность",
    "extraversion": "экстраверсия и общительность",
    "agreeableness": "доброжелательность",
    "neuroticism": "нейротизм (склонность к переживаниям и стрессу)",
}


def build_ocean_verbal_summary(
    username_a: str,
    username_b: str,
    ocean_deltas: dict[str, float],
    *,
    eps: float = 0.02,
) -> list[str]:
    """
    Короткие фразы для UI: кто сильнее по каждой оси OCEAN.
    ocean_deltas: значения (B - A), как в compare_two.
    """
    na = (username_a or "").strip() or "Пользователь A"
    nb = (username_b or "").strip() or "Пользователь B"
    lines: list[str] = []
    for k in OCEAN_KEYS:
        d = float(ocean_deltas.get(k) or 0.0)
        label = OCEAN_VERBAL_RU.get(k, k)
        if abs(d) <= eps:
            lines.append(f"По показателю «{label}» {na} и {nb} почти не различаются.")
        elif d > 0:
            lines.append(f"У {nb} выше {label}, чем у {na}.")
        else:
            lines.append(f"У {na} выше {label}, чем у {nb}.")
    return lines


def _strength_word(abs_delta: float) -> str:
    """Степень различия по шкале 0–1 для русского нарратива."""
    ad = abs(float(abs_delta))
    if ad < 0.04:
        return "незначительно"
    if ad < 0.09:
        return "умеренно"
    if ad < 0.18:
        return "заметно"
    return "существенно"


def _similarity_interpretation(sim: float) -> str:
    s = float(sim)
    if s >= 0.92:
        return "профили практически совпадают по среднему отклонению по всем пяти шкалам"
    if s >= 0.82:
        return "профили очень близки: большинство черт оценены сходно"
    if s >= 0.68:
        return "профили в целом близки, но есть отдельные акценты"
    if s >= 0.52:
        return "есть умеренный разброс по чертам — стили общения заметно различаются"
    return "наблюдается выраженный контраст по нескольким осям"


_DIM_TITLE_RU: dict[str, str] = {
    "openness": "открытость к новому опыту",
    "conscientiousness": "добросовестность",
    "extraversion": "экстраверсия",
    "agreeableness": "доброжелательность",
    "neuroticism": "нейротизм",
}


def _trait_narrative_sentence(
    trait: str,
    *,
    winner: str,
    other: str,
    strength: str,
) -> str:
    """Одно предложение-интерпретация: у winner выше показатель, чем у other."""
    t = _DIM_TITLE_RU.get(trait, trait)
    if trait == "openness":
        return (
            f"{strength.capitalize()} выражена {t} у {winner}: в переписке это может соответствовать "
            f"большей готовности к новым углам обсуждения и менее шаблонным формулировкам по сравнению с {other}."
        )
    if trait == "conscientiousness":
        return (
            f"По {t} {strength} лидирует {winner} — в текстах это иногда читается как более выстроенная "
            f"аргументация, внимание к деталям и последовательность по сравнению с {other}."
        )
    if trait == "extraversion":
        return (
            f"{strength.capitalize()} выше {t} у {winner}: в чате это может проявляться как более частая "
            f"инициатива в диалоге, энергичный тон или охват большего числа собеседников относительно {other}."
        )
    if trait == "agreeableness":
        return (
            f"По {t} {strength} сильнее проявляется у {winner} — в сообщениях это может выглядеть как более "
            f"сглаженные формулировки, ориентация на поддержку или поиск компромисса по сравнению с {other}."
        )
    if trait == "neuroticism":
        return (
            f"У {winner} {strength} выше {t} (эмоциональная реактивность): в обсуждениях это иногда "
            f"совпадает с более острыми реакциями на напряжённые темы по сравнению с {other}; "
            f"у {other} профиль в этом измерении ровнее."
        )
    return f"По показателю «{t}» {strength} выше у {winner}, чем у {other}."


def build_ocean_narrative_paragraphs(
    username_a: str,
    username_b: str,
    ocean_deltas: dict[str, float],
    similarity_score: float,
    most_similar_dimensions: list[str],
    most_different_dimensions: list[str],
    *,
    eps: float = 0.02,
) -> list[str]:
    """
    Несколько абзацев связного текста для UI (без LLM).
    Дельты: B − A. Формулировки осторожные — оценки по тексту чата, не клинический диагноз.
    """
    na = (username_a or "").strip() or "Пользователь A"
    nb = (username_b or "").strip() or "Пользователь B"
    deltas = {k: float(ocean_deltas.get(k) or 0.0) for k in OCEAN_KEYS}
    sim = max(0.0, min(1.0, float(similarity_score)))

    p1 = (
        f"Ниже — сравнение {na} и {nb} по модели Big Five (OCEAN), восстановленной из стиля сообщений в чате. "
        f"Сводный индекс схожести — {sim:.2f} (1 — максимально близкие оценки по всем шкалам); "
        f"в вашем случае это значит, что {_similarity_interpretation(sim)}. "
        f"Это не «истина о личности», а сжатое описание того, как участники выглядят в тексте относительно друг друга."
    )

    # Абзац 2: самые сильные различия (по модулю дельты)
    ranked = sorted(OCEAN_KEYS, key=lambda k: abs(deltas[k]), reverse=True)
    contrast_traits = [k for k in ranked if abs(deltas[k]) > eps][:3]
    if contrast_traits:
        chunks: list[str] = []
        for k in contrast_traits:
            d = deltas[k]
            sw = _strength_word(d)
            if d > 0:
                chunks.append(_trait_narrative_sentence(k, winner=nb, other=na, strength=sw))
            else:
                chunks.append(_trait_narrative_sentence(k, winner=na, other=nb, strength=sw))
        p2 = (
            "Самые заметные расхождения — там, где стили общения в чате расходятся сильнее всего "
            "(по оценке модели): "
            + " ".join(chunks)
        )
    else:
        p2 = (
            f"По всем пяти измерениям {na} и {nb} близки — явных «перекосов» в восстановленном профиле почти нет. "
            f"Визуально это хорошо видно на радаре: многоугольники почти совпадают."
        )

    # Абзац 3: где близки
    sim_dims = [k for k in (most_similar_dimensions or []) if k in OCEAN_KEYS]
    if not sim_dims:
        sim_dims = sorted(OCEAN_KEYS, key=lambda k: abs(deltas[k]))[:3]
    sim_labels = ", ".join(_DIM_TITLE_RU.get(k, k) for k in sim_dims)
    p3 = (
        f"Наиболее схожие измерения: {sim_labels}. "
        f"В совместных тредах это может означать общий фон по этим аспектам — меньше «ломки» стиля "
        f"именно здесь, даже если другие оси различаются."
    )

    # Абзац 4: практический смысл для чата
    top_diff = [k for k in (most_different_dimensions or []) if abs(deltas.get(k, 0)) > eps][:2]
    if top_diff:
        dl = ", ".join(_DIM_TITLE_RU.get(k, k) for k in top_diff)
        p4 = (
            f"Если смотреть на динамику чата практически, контраст по {dl} иногда даёт дополняющие роли: "
            f"один участник может сильнее «задавать тон» или удерживать план, другой — смягчать или, наоборот, "
            f"подключать острые углы. Это рабочая гипотеза для модерации, а не ярлык."
        )
    else:
        p4 = (
            f"Для модерации и тонкой настройки бота оба профиля выглядят согласованно: резких противоречий "
            f"между участниками по восстановленным шкалам мало."
        )

    p5 = (
        "Напоминание: шкалы OCEAN здесь выведены из корпуса сообщений и контекста чата; "
        "они полезны для аналитики и сравнения, но не заменяют живое наблюдение и не являются медицинским заключением."
    )

    return [p1, p2, p3, p4, p5]


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
