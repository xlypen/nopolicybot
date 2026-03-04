import json


def extract_json_text(raw: str) -> str:
    """Извлекает JSON из markdown-fenced ответа модели."""
    text = (raw or "").strip()
    if "```" not in text:
        return text
    for part in text.split("```"):
        part = part.strip()
        if part.startswith("json"):
            part = part[4:].strip()
        if "{" in part:
            return part
    return text


def parse_json(raw: str) -> dict | None:
    try:
        return json.loads(extract_json_text(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def normalize_sentiment(value: str | None) -> str:
    s = (value or "neutral").strip().lower()
    return s if s in ("positive", "negative", "neutral") else "neutral"


def normalize_message_type(value: str | None) -> str:
    mt = (value or "other").strip().lower()
    return mt if mt in ("technical_question", "general_question", "other") else "other"
