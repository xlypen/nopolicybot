"""Contextual profiles by topic (P-5 / P-12 LLM topic detection)."""

import json
import logging
import os
from collections import defaultdict

from services.personality.schema import ContextProfile, PersonalityProfile

logger = logging.getLogger(__name__)

VALID_TOPICS = {"technical", "work", "politics", "humor", "personal", "conflict", "general"}

TOPIC_MAP = {
    "technical": ("api", "сервер", "код", "ошиб", "инфра", "модел", "grok", "бот", "скрипт", "техн"),
    "work": ("работ", "задач", "срок", "проект", "релиз", "команд", "собес", "резюме"),
    "politics": ("путин", "полит", "выбор", "макрон", "зеленск", "трамп", "росси", "войн"),
    "humor": ("мем", "шут", "ирон", "рж", "ахах", "лол"),
    "personal": ("днюх", "подар", "семь", "друз", "отношен", "личн"),
}
CONFLICT_KEYWORDS = ("спор", "конфликт", "срач", "агресс", "руг", "резк", "груб", "хам", "обвинен")
TOPIC_LABELS_RU = {
    "technical": "технологии",
    "work": "работа",
    "politics": "политика",
    "humor": "юмор",
    "personal": "личное",
    "conflict": "конфликт",
    "general": "общее",
}
MIN_MESSAGES_PER_TOPIC = 15

LLM_BATCH_SIZE = 30

_TOPIC_CLASSIFY_PROMPT = """\
Classify each message into one or more topics from this list:
technical, work, politics, humor, personal, conflict, general.

Messages (index: text):
{messages_block}

Return ONLY valid JSON — an object where keys are message indices (as strings) \
and values are arrays of topic strings. Example: {{"0": ["politics"], "1": ["humor", "personal"]}}
"""


def _topic_for_message_keyword(text: str) -> str:
    """Keyword-based topic classification (fast fallback)."""
    t = (text or "").lower()
    if any(k in t for k in CONFLICT_KEYWORDS):
        return "conflict"
    for tag, kws in TOPIC_MAP.items():
        if any(k in t for k in kws):
            return tag
    return "general"


# Keep the old name as an alias so existing tests/imports keep working
_topic_for_message = _topic_for_message_keyword


def _group_messages_by_topic_keyword(messages: list[dict]) -> dict[str, list[dict]]:
    """Group messages by primary topic using keyword matching."""
    by_topic: dict[str, list[dict]] = defaultdict(list)
    for m in messages:
        text = (m.get("text") or "").strip()
        if not text:
            continue
        topic = _topic_for_message_keyword(text)
        by_topic[topic].append(m)
    return dict(by_topic)


def _llm_available() -> bool:
    """Check if OpenAI client is configured (API key present)."""
    try:
        return bool(os.getenv("OPENAI_API_KEY"))
    except Exception:
        return False


def _extract_json_object(text: str) -> dict | None:
    """Extract first JSON object from LLM response."""
    import re
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i, c in enumerate(text[start:], start):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def detect_topics_llm(messages: list[dict]) -> dict[str, list[dict]]:
    """
    LLM-based topic detection. Processes messages in batches of LLM_BATCH_SIZE.
    Falls back to keyword detection on any API error.
    Returns {topic: [messages]}.
    """
    from ai.client import get_client

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = get_client()

    by_topic: dict[str, list[dict]] = defaultdict(list)
    filtered = [(i, m) for i, m in enumerate(messages) if (m.get("text") or "").strip()]

    for batch_start in range(0, len(filtered), LLM_BATCH_SIZE):
        batch = filtered[batch_start : batch_start + LLM_BATCH_SIZE]
        messages_block = "\n".join(
            f"{idx}: {(m.get('text') or '')[:300]}" for idx, (_, m) in enumerate(batch)
        )
        prompt = _TOPIC_CLASSIFY_PROMPT.format(messages_block=messages_block)

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            content = (response.choices[0].message.content or "").strip()
            mapping = _extract_json_object(content)
            if not mapping or not isinstance(mapping, dict):
                raise ValueError("Invalid LLM response structure")

            for local_idx, (_, msg) in enumerate(batch):
                topics_raw = mapping.get(str(local_idx), [])
                if isinstance(topics_raw, str):
                    topics_raw = [topics_raw]
                topics = [t for t in topics_raw if t in VALID_TOPICS]
                if not topics:
                    topics = [_topic_for_message_keyword(msg.get("text") or "")]
                by_topic[topics[0]].append(msg)

        except Exception as e:
            logger.debug("LLM topic detection failed for batch, using keyword fallback: %s", e)
            for _, msg in batch:
                topic = _topic_for_message_keyword(msg.get("text") or "")
                by_topic[topic].append(msg)

    return dict(by_topic)


def _group_messages_by_topic(messages: list[dict]) -> dict[str, list[dict]]:
    """Group messages by primary topic. Uses LLM when available, keywords otherwise."""
    if _llm_available():
        try:
            return detect_topics_llm(messages)
        except Exception as e:
            logger.debug("LLM grouping failed, falling back to keywords: %s", e)
    return _group_messages_by_topic_keyword(messages)


def _heuristic_mini_profile(messages: list[dict], topic: str) -> ContextProfile:
    """
    Rule-based mini-profile for a topic (no LLM).
    conflict_tendency from % of conflict keywords; OCEAN from topic stereotypes + message patterns.
    """
    n = len(messages)
    conflict_count = 0
    toxic_count = 0
    question_count = 0
    long_count = 0
    for m in messages:
        t = (m.get("text") or "").lower()
        if any(k in t for k in CONFLICT_KEYWORDS):
            conflict_count += 1
        if any(x in t for x in ("нахуй", "долбо", "сука", "еб", "хуй", "мраз")):
            toxic_count += 1
        if "?" in t or "?" in (m.get("text") or ""):
            question_count += 1
        if len(t) > 100:
            long_count += 1

    conflict_tendency = min(1.0, (conflict_count + toxic_count * 2) / max(1, n) * 3)
    openness = 0.4 + 0.3 * (question_count / max(1, n)) + 0.2 * (long_count / max(1, n))
    extraversion = min(1.0, 0.3 + 0.5 * (long_count / max(1, n)))
    agreeableness = max(0.0, 0.6 - conflict_tendency * 0.5 - toxic_count / max(1, n) * 0.3)
    neuroticism = min(1.0, 0.3 + conflict_tendency * 0.4 + toxic_count / max(1, n) * 0.4)
    conscientiousness = 0.5

    topic_bias = {
        "politics": {"agreeableness": -0.1, "neuroticism": 0.15},
        "technical": {"openness": 0.1, "conscientiousness": 0.1},
        "humor": {"openness": 0.1, "agreeableness": 0.05},
        "conflict": {"agreeableness": -0.2, "neuroticism": 0.2, "conflict_tendency": 0.3},
        "personal": {"agreeableness": 0.1},
    }
    bias = topic_bias.get(topic, {})
    openness = max(0, min(1, openness + bias.get("openness", 0)))
    conscientiousness = max(0, min(1, conscientiousness + bias.get("conscientiousness", 0)))
    agreeableness = max(0, min(1, agreeableness + bias.get("agreeableness", 0)))
    neuroticism = max(0, min(1, neuroticism + bias.get("neuroticism", 0)))
    conflict_tendency = max(0, min(1, conflict_tendency + bias.get("conflict_tendency", 0)))

    return ContextProfile(
        messages_count=n,
        ocean={
            "openness": round(openness, 3),
            "conscientiousness": round(conscientiousness, 3),
            "extraversion": round(extraversion, 3),
            "agreeableness": round(agreeableness, 3),
            "neuroticism": round(neuroticism, 3),
        },
        conflict_tendency=round(conflict_tendency, 3),
    )


def build_context_profiles(messages: list[dict], base_profile: PersonalityProfile | None = None) -> dict[str, ContextProfile]:
    """
    Build mini-profiles per topic for messages.
    Returns dict topic_key -> ContextProfile for topics with >= MIN_MESSAGES_PER_TOPIC messages.
    """
    by_topic = _group_messages_by_topic(messages)
    result: dict[str, ContextProfile] = {}
    for topic, msgs in by_topic.items():
        if len(msgs) < MIN_MESSAGES_PER_TOPIC:
            continue
        result[topic] = _heuristic_mini_profile(msgs, topic)
    return result


def enrich_profile_with_context(profile: PersonalityProfile, messages: list[dict]) -> PersonalityProfile:
    """Add context_profiles to an existing profile."""
    ctx = build_context_profiles(messages, profile)
    profile.context_profiles = ctx
    return profile
