"""
IMG-1: Трансляция структурированного профиля личности в промпт для image-генерации.

Генерируемое изображение — визуальная метафора личности: архетипический персонаж
в соответствующей среде, с цветовой палитрой и настроением из данных OCEAN.
НЕ портрет реального человека.
"""

from __future__ import annotations

from services.personality.schema import PersonalityProfile

OPENNESS_MAP = {
    (0.0, 0.35): "confined interior, familiar objects, warm but limited space",
    (0.35, 0.65): "mixed interior and exterior, books and tools nearby",
    (0.65, 1.01): "vast open landscape or library without walls, unusual objects, surreal touches",
}

CONSCIENTIOUSNESS_MAP = {
    (0.0, 0.35): "chaotic desk, scattered papers, informal clothing",
    (0.35, 0.65): "organized but lived-in workspace",
    (0.65, 1.01): "immaculate environment, precise geometry, structured composition",
}

EXTRAVERSION_MAP = {
    (0.0, 0.35): "solitary figure, empty room or forest, facing away or in shadow",
    (0.35, 0.65): "small group, one-on-one interaction suggested",
    (0.65, 1.01): "central figure in a crowd or gathering, dynamic pose, eye contact",
}

AGREEABLENESS_MAP = {
    (0.0, 0.35): "guarded posture, cold color temperature, distance from others",
    (0.35, 0.65): "neutral stance, moderate warmth",
    (0.65, 1.01): "open gesture, warm golden light, soft focus edges",
}

NEUROTICISM_MAP = {
    (0.0, 0.35): "calm scene, still water or clear sky, stable composition",
    (0.35, 0.65): "slightly overcast, subtle tension in composition",
    (0.65, 1.01): "stormy atmosphere, dramatic shadows, asymmetric tension",
}

DARK_TRIAD_MAP = {
    "low": "honest expression, natural environment",
    "medium": "strategic gaze, formal or controlled environment",
    "high": "cold calculated atmosphere, chess metaphor, empty formal space",
}

ROLE_MAP = {
    "connector": "figure at the center of intersecting paths, bridge or crossroads",
    "expert": "scholar with manuscripts, telescope or laboratory",
    "mediator": "peacemaker between two groups, hands extended",
    "provocateur": "figure pointing or speaking boldly, audience reacting",
    "follower": "figure in a crowd, slightly behind others",
    "lurker": "observer in shadow watching an illuminated scene",
}

STYLE_VARIANTS = {
    "concept_art": {
        "suffix": "character concept art, detailed illustration, painterly, cinematic",
        "description": "Художественный концепт-арт",
    },
    "symbolic": {
        "suffix": (
            "symbolic abstract art, metaphorical composition, surreal elements, "
            "award-winning digital art"
        ),
        "description": "Символическая абстракция",
    },
    "noir": {
        "suffix": (
            "noir photography style, black and white with one accent color, "
            "dramatic shadows, film grain"
        ),
        "description": "Нуар / чёрно-белый",
    },
    "fantasy": {
        "suffix": (
            "fantasy character portrait, magical atmosphere, ethereal lighting, "
            "detailed environment"
        ),
        "description": "Фэнтезийный образ",
    },
    "minimal": {
        "suffix": (
            "minimalist illustration, clean lines, limited palette, "
            "negative space composition"
        ),
        "description": "Минималистичный",
    },
}

DEFAULT_STYLE = "concept_art"

NEGATIVE_PROMPT = (
    "photo, realistic face, identifiable person, text, watermark, "
    "ugly, deformed, nsfw, low quality, blurry"
)


def _pick(mapping: dict[tuple[float, float], str], value: float) -> str:
    for (lo, hi), desc in mapping.items():
        if lo <= value < hi:
            return desc
    return list(mapping.values())[-1]


def _compute_mood(profile: PersonalityProfile) -> str:
    valence = profile.emotional_profile.valence
    arousal = profile.emotional_profile.arousal
    if valence > 0.6 and arousal > 0.6:
        return "energetic and optimistic atmosphere"
    if valence > 0.6:
        return "peaceful and content atmosphere"
    if valence <= 0.4 and arousal > 0.6:
        return "tense and restless atmosphere"
    return "melancholic and introspective atmosphere"


def _compute_palette(profile: PersonalityProfile) -> str:
    o = profile.ocean
    if o.agreeableness > 0.6 and o.neuroticism < 0.4:
        return "warm golden and amber tones"
    if o.neuroticism > 0.6:
        return "cold blue and grey tones with deep shadows"
    if o.openness > 0.7:
        return "rich varied palette, unexpected color combinations"
    return "neutral desaturated palette"


def build_image_prompt(
    profile: PersonalityProfile,
    *,
    style_variant: str = DEFAULT_STYLE,
) -> dict:
    """
    Собирает промпт для image-генерации из структурированного профиля.

    Returns dict:
      positive_prompt  — основной промпт
      negative_prompt  — что исключить
      style_hint       — стиль
      seed_description — краткое описание для логирования
    """
    ocean = profile.ocean

    dt_risk = max(
        profile.dark_triad.narcissism.score,
        profile.dark_triad.machiavellianism.score,
        profile.dark_triad.psychopathy.score,
    )
    dt_label = "low" if dt_risk < 0.33 else ("medium" if dt_risk < 0.66 else "high")

    mood = _compute_mood(profile)
    palette = _compute_palette(profile)

    style = STYLE_VARIANTS.get(style_variant, STYLE_VARIANTS[DEFAULT_STYLE])

    elements = [
        "A lone archetypal figure,",
        _pick(EXTRAVERSION_MAP, ocean.extraversion) + ",",
        _pick(OPENNESS_MAP, ocean.openness) + ",",
        _pick(CONSCIENTIOUSNESS_MAP, ocean.conscientiousness) + ",",
        _pick(AGREEABLENESS_MAP, ocean.agreeableness) + ",",
        _pick(NEUROTICISM_MAP, ocean.neuroticism) + ",",
        DARK_TRIAD_MAP[dt_label] + ",",
    ]

    role_desc = ROLE_MAP.get(profile.role_in_community)
    if role_desc:
        elements.append(role_desc + ",")

    elements.extend([
        mood + ",",
        palette + ",",
        style["suffix"],
    ])

    positive = " ".join(e for e in elements if e.strip(",").strip())

    return {
        "positive_prompt": positive,
        "negative_prompt": NEGATIVE_PROMPT,
        "style_hint": style_variant,
        "style_description": style["description"],
        "seed_description": (
            f"OCEAN({ocean.openness:.2f},{ocean.extraversion:.2f},"
            f"{ocean.agreeableness:.2f},{ocean.conscientiousness:.2f},"
            f"{ocean.neuroticism:.2f}) dt={dt_label} role={profile.role_in_community}"
        ),
    }


def build_image_prompt_from_dict(profile_data: dict, **kwargs) -> dict:
    """Обёртка для dict (из DB profile_json)."""
    profile = PersonalityProfile.model_validate(profile_data)
    return build_image_prompt(profile, **kwargs)
