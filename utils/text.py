"""Общие текстовые утилиты: обрезка по границе слова, превью summary."""

import re


def soft_trim(text: str, max_len: int = 400) -> str:
    """Обрезает текст по границе предложения или слова, не режет посередине слова."""
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    chunk = t[: max_len + 1]
    # Сначала ищем конец предложения в последней половине
    for sep in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        pos = chunk.rfind(sep)
        if pos > max_len // 2:
            return chunk[: pos + len(sep)].strip().rstrip(".,") + " …"
    # Потом — точку с запятой, запятую
    for sep in ("; ", ", "):
        pos = chunk.rfind(sep)
        if pos > max_len // 2:
            return chunk[: pos + len(sep)].strip() + " …"
    # В конце — по границе слова (последний пробел)
    last_space = chunk.rfind(" ")
    if last_space > 0:
        return chunk[:last_space].rstrip().rstrip(".,;") + " …"
    return chunk[:max_len].rstrip() + " …"


def summary_preview(text: str, max_len: int = 420) -> tuple[str, bool]:
    """Превью summary: берёт самый свежий блок, нормализует пробелы, обрезка по границе слова."""
    s = (text or "").strip()
    if not s:
        return "", False
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if lines:
        s = lines[-1]
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= max_len:
        return s, False
    trimmed = soft_trim(s, max_len)
    return trimmed, True
