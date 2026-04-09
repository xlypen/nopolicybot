"""Build structured personality profile from messages (P-2)."""

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from ai.client import chat_complete_with_fallback, get_client, prefer_free_mode
from openai import APIError, RateLimitError

from services.personality.contextual import enrich_profile_with_context
from services.personality.schema import OCEAN_KEYS, PersonalityProfile
from pydantic import ValidationError

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


def _clamp01(x, default: float = 0.5) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    if v < 0.0:
        return 0.0
    if v <= 1.0:
        return v
    if v > 100.0:
        return 1.0
    # 1 < v <= 100: проценты (напр. 72) или слегка вышедшее за 1.0 (1.2)
    if v >= 10.0:
        return min(1.0, v / 100.0)
    return min(1.0, v)


_COMM_STYLES = frozenset({"assertive", "passive", "aggressive", "passive-aggressive"})
_DT_LABELS = frozenset({"low", "medium", "high"})


def _sanitize_profile_dict(data: dict) -> dict:
    """Coerce LLM JSON into schema-friendly shapes (ranges, enums)."""
    out = dict(data)
    oc = out.get("ocean")
    if isinstance(oc, dict):
        for k in OCEAN_KEYS:
            if k in oc:
                oc[k] = _clamp01(oc[k], 0.5)
        out["ocean"] = oc

    dt = out.get("dark_triad")
    if isinstance(dt, dict):
        for key in ("narcissism", "machiavellianism", "psychopathy"):
            t = dt.get(key)
            if not isinstance(t, dict):
                continue
            sc = _clamp01(t.get("score", 0.2), 0.2)
            lb = str(t.get("label", "low")).strip().lower()
            if lb not in _DT_LABELS:
                lb = "low" if sc < 0.34 else "high" if sc > 0.66 else "medium"
            t = dict(t)
            t["score"] = sc
            t["label"] = lb
            dt[key] = t
        out["dark_triad"] = dt

    comm = out.get("communication")
    if isinstance(comm, dict):
        st = str(comm.get("style", "assertive")).strip().lower()
        if st not in _COMM_STYLES:
            st = "assertive"
        comm = dict(comm)
        comm["style"] = st
        for fld in ("conflict_tendency", "influence_seeking", "emotional_expressiveness", "topic_consistency"):
            if fld in comm:
                comm[fld] = _clamp01(comm[fld], 0.5)
        out["communication"] = comm

    emo = out.get("emotional_profile")
    if isinstance(emo, dict):
        emo = dict(emo)
        if "valence" in emo:
            emo["valence"] = _clamp01(emo["valence"], 0.5)
        if "arousal" in emo:
            emo["arousal"] = _clamp01(emo["arousal"], 0.5)
        de = emo.get("dominant_emotions")
        if de is None:
            emo["dominant_emotions"] = []
        elif isinstance(de, list):
            emo["dominant_emotions"] = [str(x).strip() for x in de if str(x).strip()][:10]
        else:
            emo["dominant_emotions"] = []
        out["emotional_profile"] = emo

    top = out.get("topics")
    if isinstance(top, dict):
        top = dict(top)
        for k in ("primary", "secondary", "avoided"):
            v = top.get(k)
            if not isinstance(v, list):
                top[k] = []
            else:
                top[k] = [str(x).strip() for x in v if str(x).strip()][:20]
        out["topics"] = top

    if "confidence" in out:
        out["confidence"] = _clamp01(out["confidence"], 0.5)

    return out


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
    *,
    skip_context_enrich: bool = False,
) -> PersonalityProfile | None:
    """
    Build structured personality profile from messages.
    Returns PersonalityProfile or None on failure.
    skip_context_enrich=True — не вызывать enrich_profile_with_context (дорогие батчи LLM по темам);
    вызывайте enrich один раз на итоговом профиле (ансамбль).
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

    last_error = ""
    msgs = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(max_retries + 1):
        try:
            content, model_used = chat_complete_with_fallback(
                messages=msgs,
                model=model,
                max_tokens=2048,
                temperature=0.3,
                prefer_free=prefer_free_mode(),
            )
            if not content:
                last_error = f"Empty response from {model_used}"
                continue

            raw = _extract_json(content)
            data = json.loads(raw)

            for key in ("ocean", "dark_triad", "communication", "emotional_profile", "topics"):
                if key not in data:
                    data[key] = {}

            data["user_id"] = str(user_id)
            data["username"] = username or str(user_id)
            data["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            data["period_days"] = period_days
            data["messages_analyzed"] = len(messages)
            data.setdefault("confidence", min(0.9, 0.5 + len(messages) / 500 * 0.2))

            data = _sanitize_profile_dict(data)
            try:
                profile = PersonalityProfile.model_validate(data)
            except ValidationError as ve:
                last_error = f"Validation: {ve}"
                if attempt < max_retries:
                    msgs[-1]["content"] += (
                        f"\n\n[RETRY] JSON failed schema validation: {ve}. "
                        "Fix ocean 0..1, communication.style assertive|passive|aggressive|passive-aggressive, "
                        "dark_triad labels low|medium|high. Return ONLY valid JSON."
                    )
                    continue
                logger.warning("Personality profile validation failed: %s", ve)
                return None
            if skip_context_enrich:
                return profile
            return enrich_profile_with_context(profile, messages)

        except json.JSONDecodeError as e:
            last_error = f"Invalid JSON: {e}"
            if attempt < max_retries:
                msgs[-1]["content"] += f"\n\n[RETRY] Previous response had invalid JSON. Error: {e}. Return ONLY valid JSON."
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
