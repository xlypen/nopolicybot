"""Personality comparison and community clustering (P-6)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

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
            f"У {winner} {strength} заметнее «игра с идеями»: готовность к неожиданным ассоциациям, иронии "
            f"и смене ракурса — на фоне {other} это выглядит как чуть более пластичный интеллектуальный темп."
        )
    if trait == "conscientiousness":
        return (
            f"{winner} {strength} чаще «держит линию»: в текстах проступают выверенность формулировок, "
            f"опора на факты и последовательность шагов — у {other} тот же чат может читаться свободнее или рванее."
        )
    if trait == "extraversion":
        return (
            f"У {winner} {strength} выше заряд «социальной батареи» в переписке: чаще заводит тему, "
            f"подхватывает тред, задаёт ритм — {other} в этой паре может звучать чуть более камерно или выборочно."
        )
    if trait == "agreeableness":
        return (
            f"{winner} {strength} сильнее «смазывает углы»: больше поддержки, меньше колкости в лексике, "
            f"больше сигналов сотрудничества — на контрасте с {other}, где тон может быть плотнее или прямее."
        )
    if trait == "neuroticism":
        return (
            f"У {winner} {strength} выше эмоциональная амплитуда по тексту: острее реагирует на напряжение "
            f"и неопределённость; у {other} в тех же обсуждениях чаще ощущается более ровный, «спокойный» фон."
        )
    return f"По показателю «{t}» {strength} выше у {winner}, чем у {other}."


TRAIT_BULLET_EMOJI: dict[str, str] = {
    "openness": "🧠",
    "conscientiousness": "📐",
    "extraversion": "⚡",
    "agreeableness": "🤝",
    "neuroticism": "🌊",
}


def build_ocean_narrative_sections(
    username_a: str,
    username_b: str,
    ocean_deltas: dict[str, float],
    similarity_score: float,
    most_similar_dimensions: list[str],
    most_different_dimensions: list[str],
    *,
    eps: float = 0.02,
) -> list[dict[str, Any]]:
    """
    Структурированный разбор для богатого UI: эмодзи, подзаголовки, маркеры.
    Дельты: B − A. Без LLM; формулировки осторожные.
    """
    na = (username_a or "").strip() or "Пользователь A"
    nb = (username_b or "").strip() or "Пользователь B"
    deltas = {k: float(ocean_deltas.get(k) or 0.0) for k in OCEAN_KEYS}
    sim = max(0.0, min(1.0, float(similarity_score)))

    intro_p1 = (
        f"Это не анкета и не диагноз — скорее два «отпечатка стиля»: как в тексте чата проступают {na} и {nb}, "
        f"если смотреть через призму Big Five (OCEAN). Модель сравнивает не людей целиком, а то, "
        f"как они звучат рядом друг с другом в переписке."
    )
    intro_p2 = (
        f"Индекс схожести {sim:.2f} из 1.00 — {_similarity_interpretation(sim)}. "
        f"Чем он выше, тем спокойнее часто ощущается «музыка» диалога; чем ниже — тем заметнее могут быть разные привычки "
        f"в споре, шутке или поддержке. Ниже — разбор по слоям."
    )

    ranked = sorted(OCEAN_KEYS, key=lambda k: abs(deltas[k]), reverse=True)
    contrast_traits = [k for k in ranked if abs(deltas[k]) > eps][:3]

    sections: list[dict[str, Any]] = [
        {
            "emoji": "✨",
            "title": "Два голоса в одном чате",
            "subtitle": "OCEAN · восстановлено из стиля сообщений",
            "paragraphs": [intro_p1, intro_p2],
            "bullets": [],
        }
    ]

    if contrast_traits:
        bullets: list[str] = []
        for k in contrast_traits:
            d = deltas[k]
            sw = _strength_word(d)
            em = TRAIT_BULLET_EMOJI.get(k, "·")
            lab = _DIM_TITLE_RU.get(k, k).capitalize()
            if d > 0:
                sent = _trait_narrative_sentence(k, winner=nb, other=na, strength=sw)
            else:
                sent = _trait_narrative_sentence(k, winner=na, other=nb, strength=sw)
            bullets.append(f"{em} {lab}: {sent}")
        sections.append({
            "emoji": "⚡",
            "title": "Где контуры расходятся",
            "subtitle": "Три самых глубоких различия (по модулю Δ)",
            "paragraphs": [
                "Здесь не «кто лучше», а где статистически чаще виден разный настрой в формулировках — "
                "то, что модератор или бот могут учитывать как зоны повышенного трения или, наоборот, дополнения.",
            ],
            "bullets": bullets,
        })
    else:
        sections.append({
            "emoji": "◎",
            "title": "Почти совпадение",
            "subtitle": "По всем осям профили близки",
            "paragraphs": [
                f"По всем пяти измерениям {na} и {nb} почти не расходятся — на радаре многоугольники почти ложатся друг на друга. "
                f"Это редкий случай «общего темпа» в тексте.",
            ],
            "bullets": [],
        })

    sim_dims = [k for k in (most_similar_dimensions or []) if k in OCEAN_KEYS]
    if not sim_dims:
        sim_dims = sorted(OCEAN_KEYS, key=lambda k: abs(deltas[k]))[:3]
    sim_labels = ", ".join(_DIM_TITLE_RU.get(k, k) for k in sim_dims)
    sections.append({
        "emoji": "🤝",
        "title": "Общий фон",
        "subtitle": f"Ближе всего: {sim_labels}",
        "paragraphs": [
            f"На этих осях {na} и {nb} звучат на похожей ноте — меньше риска «ломки» стиля именно здесь. "
            f"Даже когда другие измерения спорят, этот слой даёт общую почву: проще понимать друг друга без лишней интерпретации.",
        ],
        "bullets": [],
    })

    top_diff = [k for k in (most_different_dimensions or []) if abs(deltas.get(k, 0)) > eps][:2]
    if top_diff:
        dl = ", ".join(_DIM_TITLE_RU.get(k, k) for k in top_diff)
        dyn_p = (
            f"Контраст по {dl} иногда складывается в мини-сценарий: один сильнее тянет на себя структуру и тон треда, "
            f"другой — эмоциональный цвет или смягчение. Такие пары часто «дополняют» друг друга в дискуссии — "
            f"если не доводить разницу до поляризации. Используйте это как рабочую гипотезу для модерации, не как ярлык."
        )
    else:
        dyn_p = (
            f"По восстановленным шкалам {na} и {nb} выглядят согласованно: мало резких противоречий — "
            f"боту и админу проще держать единый тон ответов."
        )
    sections.append({
        "emoji": "🎭",
        "title": "Хореография диалога",
        "subtitle": "Как различия могут проявляться в живом чате",
        "paragraphs": [dyn_p],
        "bullets": [],
    })

    sections.append({
        "emoji": "📎",
        "title": "Важно помнить",
        "subtitle": None,
        "paragraphs": [
            "Шкалы выведены из корпуса сообщений и контекста чата: они хороши для аналитики и тонкой настройки, "
            "но не заменяют живое наблюдение и не являются медицинским или юридическим заключением.",
        ],
        "bullets": [],
    })

    return sections


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
    """Плоский список абзацев (совместимость): все paragraphs + bullets по порядку секций."""
    out: list[str] = []
    for block in build_ocean_narrative_sections(
        username_a,
        username_b,
        ocean_deltas,
        similarity_score,
        most_similar_dimensions,
        most_different_dimensions,
        eps=eps,
    ):
        out.extend(block.get("paragraphs") or [])
        out.extend(block.get("bullets") or [])
    return out


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
