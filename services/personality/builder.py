"""Build structured personality profile from messages (P-2)."""

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from ai.client import get_client
from openai import APIError, RateLimitError

from services.personality.schema import PersonalityProfile

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_SCHEMA_EXAMPLE = """{
  "ocean": {"openness": 0.5, "conscientiousness": 0.5, "extraversion": 0.5, "agreeableness": 0.5, "neuroticism": 0.5},
  "dark_triad": {
    "narcissism": {"label": "low", "score": 0.2},
    "machiavellianism": {"label": "low", "score": 0.2},
    "psychopathy": {"label": "low", "score": 0.2}
  },
  "communication": {"style": "assertive", "conflict_tendency": 0.5, "influence_seeking": 0.5, "emotional_expressiveness": 0.5, "topic_consistency": 0.5},
  "emotional_profile": {"valence": 0.5, "arousal": 0.5, "dominant_emotions": []},
  "topics": {"primary": [], "secondary": [], "avoided": []},
  "role_in_community": "",
  "summary": "Краткое резюме 2–3 предложения на русском.",
  "confidence": 0.7
}"""


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def _extract_json(text: str) -> str:
    """Extract JSON from LLM response (handle markdown code blocks)."""
    text = (text or "").strip()
    # Try ```json ... ``` or ``` ... ```
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        return m.group(1).strip()
    # Try { ... } from start
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i, c in enumerate(text[start:], start):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return text


def _format_messages(messages: list[dict], max_chars: int = 60000) -> str:
    """Format messages for prompt. messages: [{text, date}] or [{text, date, chat_id}]."""
    lines = []
    total = 0
    for m in reversed(messages[-1000:]):
        t = (m.get("text") or "").strip()
        if not t:
            continue
        d = (m.get("date") or "")[:10]
        line = f"[{d}] {t[:500]}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(reversed(lines[-500:]))


def build_structured_profile_from_messages(
    messages: list[dict],
    user_id: str | int,
    username: str = "",
    period_days: int = 30,
    chat_description: str = "Telegram chat",
    model: str | None = None,
    max_retries: int = 2,
) -> PersonalityProfile | None:
    """
    Build structured personality profile from messages.
    Returns PersonalityProfile or None on failure.
    """
    if not messages:
        return None

    import os

    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    system_prompt = _load_prompt("profile_system")
    user_template = _load_prompt("profile_user")

    messages_text = _format_messages(messages)
    if not messages_text:
        return None

    user_prompt = user_template.format(
        messages_count=len(messages),
        username=username or str(user_id),
        user_id=str(user_id),
        period_days=period_days,
        chat_description=chat_description,
        messages_text=messages_text,
        schema_json=_SCHEMA_EXAMPLE,
    )

    client = get_client()
    last_error = ""

    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
            )
            content = (response.choices[0].message.content or "").strip()
            if not content:
                last_error = "Empty response"
                continue

            raw = _extract_json(content)
            data = json.loads(raw)

            # Ensure required structure
            if "ocean" not in data:
                data["ocean"] = {}
            if "dark_triad" not in data:
                data["dark_triad"] = {}
            if "communication" not in data:
                data["communication"] = {}
            if "emotional_profile" not in data:
                data["emotional_profile"] = {}
            if "topics" not in data:
                data["topics"] = {}

            # Fill from context
            data["user_id"] = str(user_id)
            data["username"] = username or str(user_id)
            data["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            data["period_days"] = period_days
            data["messages_analyzed"] = len(messages)
            data.setdefault("confidence", min(0.9, 0.5 + len(messages) / 500 * 0.2))

            profile = PersonalityProfile.model_validate(data)
            return profile

        except json.JSONDecodeError as e:
            last_error = f"Invalid JSON: {e}"
            if attempt < max_retries:
                user_prompt += f"\n\n[RETRY] Previous response had invalid JSON. Error: {e}. Return ONLY valid JSON."
        except Exception as e:
            if isinstance(e, (RateLimitError, APIError)):
                if attempt < max_retries:
                    time.sleep(10 + attempt * 5)
                    continue
            last_error = str(e)
            logger.exception("Personality profile build failed: %s", e)
            return None

    logger.warning("Personality profile build failed after retries: %s", last_error)
    return None
