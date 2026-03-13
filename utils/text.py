"""Общие текстовые утилиты: обрезка по границе слова, превью summary."""

import re


def soft_trim(text: str, max_len: int = 400) -> str:
    """Обрезает текст по границе слова/предложения."""
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    chunk = t[:max_len]
    for sep in (". ", "! ", "? ", "\n", "; ", ", ", " "):
        pos = chunk.rfind(sep)
        if pos >= max_len * 0.6:
            return chunk[: pos + len(sep)].strip() + " …"
    return chunk.rstrip() + " …"


def summary_preview(text: str, max_len: int = 420) -> tuple[str, bool]:
    """Превью summary: берёт самый свежий блок, нормализует пробелы."""
    s = (text or "").strip()
    if not s:
        return "", False
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if lines:
        s = lines[-1]
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= max_len:
        return s, False
    chunk = s[:max_len]
    for sep in (". ", "! ", "? ", "; ", ", ", " "):
        pos = chunk.rfind(sep)
        if pos >= max_len * 0.55:
            return chunk[: pos + len(sep)].strip() + " …", True
    return chunk.rstrip() + " …", True
