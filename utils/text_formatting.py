import re
from html import escape


def capitalize_sentences(text: str) -> str:
    """Второе и каждое последующее предложение начинается с большой буквы."""
    if not text or len(text) < 2:
        return text
    result = []
    capitalize_next = False
    for ch in text:
        if capitalize_next and ch.isalpha():
            result.append(ch.upper())
            capitalize_next = False
        else:
            result.append(ch)
            if ch in ".!?":
                capitalize_next = True
    return "".join(result)


def reply_text_to_html(text: str) -> str:
    """
    Конвертирует markdown-блоки ``` в HTML для Telegram (parse_mode=HTML).
    Код внутри блоков — в <pre><code>...</code></pre>, остальной текст — escaped.
    """
    if not text or not text.strip():
        return ""
    result = []
    last_end = 0
    for m in re.finditer(r"```(\w*)\n(.*?)```", text, re.DOTALL):
        before = text[last_end : m.start()]
        if before:
            result.append(escape(before))
        code = m.group(2).rstrip()
        code_escaped = escape(code)
        result.append(f"<pre><code>{code_escaped}</code></pre>")
        last_end = m.end()
    if last_end < len(text):
        result.append(escape(text[last_end:]))
    return "".join(result)


def strip_leading_name(reply_text: str, first_name: str, user_name: str = "") -> str:
    """
    Убирает из начала ответа имя/ник вида `Имя, ...`, чтобы не дублировать упоминание в Telegram.
    """
    cleaned = (reply_text or "").strip()
    names_to_strip = [first_name] if first_name else []
    if user_name and user_name != first_name:
        names_to_strip.append(user_name)
    while True:
        changed = False
        for name in names_to_strip:
            for sep in (",", "!", " ", ":", "，"):
                prefix = name + sep
                if cleaned.lower().startswith(prefix.lower()):
                    cleaned = cleaned[len(prefix):].strip()
                    changed = True
                    break
            if changed:
                break
        if not changed:
            break
    return cleaned
