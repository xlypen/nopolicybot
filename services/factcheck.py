"""
Факт-чек высказываний: разбор на факты, поиск в интернете, верификация.
Проверяем только проверяемые утверждения (не мнения, не личное).
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parent.parent / ".factcheck_cache"
_CACHE_TTL_SEC = 48 * 3600  # 48 часов
_MAX_FACTS = 5
_MAX_QUERIES_PER_FACT = 5
_MAX_SEARCH_RESULTS = 12


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()[:16]


def _cache_get(key: str) -> str | None:
    if not _CACHE_DIR.exists():
        return None
    p = _CACHE_DIR / f"{key}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) > _CACHE_TTL_SEC:
            p.unlink(missing_ok=True)
            return None
        return data.get("result")
    except Exception:
        return None


def _cache_set(key: str, result: str) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = _CACHE_DIR / f"{key}.json"
        p.write_text(json.dumps({"ts": time.time(), "result": result}, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.debug("Factcheck cache write: %s", e)


EXTRACT_FACTS_PROMPT = """Извлеки ВСЕ проверяемые утверждения из высказывания. Чекать нужно ЖЁСТКО и тщательно.

ОБЯЗАТЕЛЬНО извлекай:
- Сравнения: «экономика X слабее/сильнее Y», «X больше/меньше Y» — проверяемо через ВВП, статистику
- Экономические: курсы, цены, ВВП, инфляция, доходы компаний
- Технические: характеристики, версии ПО, даты релизов
- Политические: заявления, законы, события, цитаты
- Статистика, цифры, даты — всё, что можно сверить

Для каждого факта: сформулируй чёткое утверждение и 2–3 разные поисковые запроса (на русском и/или английском), чтобы найти пруфы.

ВАЖНО: сравнения «слабее», «сильнее», «больше», «меньше» — проверяемы (ВВП, статистика). Компании, лоббизм, госзаказы — тоже. Оскорбления в начале — игнорируй.

НЕ включай: только мнения без конкретики, личный опыт, шутки.

Высказывание: "{text}"

Ответ СТРОГО JSON:
{{"facts": [
  {{"claim": "точная формулировка проверяемого утверждения", "queries": ["запрос 1", "запрос 2", "запрос 3"]}},
  ...
]}}

Извлеки до 5 фактов. Если есть хоть одно проверяемое утверждение — верни минимум 1 факт."""

VERIFY_FACT_PROMPT = """Проверь факт по результатам поиска. Строго и тщательно.

Факт: "{claim}"

Результаты поиска:
{search_results}

Определи вердикт строго по фактам, БЕЗ упоминания технических деталей:
- confirmed: подтверждено надёжными источниками (новости, официальные, рейтинговые)
- debunked: опровергнуто или источники противоречат утверждению
- unclear: недостаточно данных или противоречивые источники

Требования:
- обязательно укажи 1–2 конкретных источника (название + URL если есть);
- если хотя бы один серьёзный источник (например, Википедия, Всемирный банк, МВФ, крупные СМИ) однозначно опровергает утверждение — ставь debunked, даже если других источников мало;
- НЕЛЬЗЯ упоминать ошибки, HTTP-коды, API, модели, провайдеров, «процедуру проверки» и т.п. Говори только про факты и источники;
- обоснование кратко, по фактам, без технических подробностей о том, как выполнялась проверка.

Ответ СТРОГО JSON:
{{"verdict": "confirmed"|"debunked"|"unclear", "sources": ["источник 1", "источник 2"], "reasoning": "обоснование"}}"""

FINAL_SUMMARY_PROMPT = """Итог факт-чека. КРАТКО, по делу. Максимум 5 коротких предложений.

Данные (сырые результаты проверки, НЕ цитируй их дословно):
{verifications}

Формат: один абзац. Вердикт (подтверждено/частично/не подтверждено). По каждому факту — суть + вердикт + источник (URL если есть).
НЕЛЬЗЯ упоминать ошибки, HTTP-коды, API, модели, провайдеров, кэш, «процедуру проверки» или внутреннюю кухню. Только человеческое резюме проверки по источникам, без техподробностей и извинений."""


_COUNTRY_EN = {
    "молдов": "Moldova", "молдави": "Moldova",
    "рф": "Russia", "росси": "Russia", "россия": "Russia",
    "сша": "USA", "америк": "USA", "соединённ": "USA",
    "украин": "Ukraine", "белорус": "Belarus", "казахстан": "Kazakhstan",
    "кита": "China", "германи": "Germany", "франци": "France",
    "великобритан": "UK", "британи": "UK", "япони": "Japan",
}


def _search_web(query: str, max_results: int = 5) -> list[dict]:
    """Поиск через ddgs. Retry + fallback на другие бэкенды при пустом результате."""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        q = query.strip()[:150]
        if not q:
            return []
        # Регион: us-en для англ. запросов (больше Wikipedia, World Bank), ru-ru для русских
        region = "us-en" if any(c in "abcdefghijklmnopqrstuvwxyz" for c in q.lower()) else "ru-ru"
        backends = ["auto", "bing", "brave"]
        for backend in backends:
            try:
                results = list(DDGS().text(q, region=region, max_results=max_results, backend=backend))
                out = [{"title": r.get("title", ""), "body": r.get("body", ""), "href": r.get("href", r.get("url", ""))} for r in results]
                if out:
                    return out
                if backend != backends[-1]:
                    time.sleep(1.5)
            except Exception as be:
                logger.debug("Factcheck search backend %s: %s", backend, be)
                time.sleep(1.5)
        return []
    except Exception as e:
        logger.warning("Factcheck search: %s", e)
        return []


def _chat_with_fallback(prompt: str, max_tokens: int = 500, temperature: float = 0.2) -> str:
    """Вызов ИИ с fallback на бесплатные модели при 402."""
    from ai.client import get_client, get_chat_models_for_fallback, _is_402
    from ai.parsers import parse_json
    client = get_client()
    models = get_chat_models_for_fallback()
    last_err = None
    for model_id in models:
        try:
            resp = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            last_err = e
            if _is_402(e):
                logger.info("Factcheck: модель %s — 402, пробуем следующую", model_id)
                continue
            raise
    if last_err:
        raise last_err
    return ""


_COMPARISON_TRIGGERS = ("слабее", "сильнее", "богаче", "беднее", "больше", "меньше", "экономик", "ввп", "росси", "рф", "сша", "молдов", "украин", "gdp")


def _extract_facts_fallback(text: str) -> list[dict]:
    """Эвристика: когда ИИ вернул пусто — извлекаем утверждения по предложениям."""
    if not text or len(text) < 20:
        return []
    import re
    t = text.strip().lower()
    # Сравнения экономик/стран — всегда один факт + англ. запросы для GDP
    if any(tr in t for tr in _COMPARISON_TRIGGERS) and len(text) >= 20:
        queries = [text.strip()[:80]]
        words = [w for w in re.findall(r"[а-яёa-z0-9]+", t) if len(w) > 2][:6]
        if words:
            queries.append(" ".join(words[:5]))
        # Англ. запросы для ВВП — дают Wikipedia, World Bank, IMF
        countries = [v for k, v in _COUNTRY_EN.items() if k in t]
        if len(countries) >= 2:
            queries.append(f"GDP {countries[0]} {countries[1]} comparison")
            queries.append(f"{countries[0]} vs {countries[1]} economy")
        elif len(countries) == 1:
            queries.append(f"GDP {countries[0]} economy")
        return [{"claim": text.strip()[:200], "queries": list(dict.fromkeys(queries))[:5]}]
    # Разбиваем на предложения; если нет — весь текст как один факт
    parts = re.split(r"[.:]\s+", text)
    if not any(p.strip() for p in parts):
        parts = [text.strip()]
    out = []
    seen = set()
    for p in parts:
        p = p.strip()
        if not p or len(p) < 20 or len(p) > 300:
            continue
        # Пропускаем мусор
        if any(x in p.lower() for x in ("сука", "блядь", "ешь", "на,")):
            continue
        key = p[:50].lower()
        if key in seen:
            continue
        seen.add(key)
        # Создаём поисковые запросы
        queries = [p[:80]]
        words = [w for w in re.findall(r"[а-яёa-z0-9]+", p.lower()) if len(w) > 3][:5]
        if words:
            queries.append(" ".join(words[:4]))
        out.append({"claim": p[:200], "queries": queries[:3]})
        if len(out) >= _MAX_FACTS:
            break
    return out


def _preprocess_for_factcheck(text: str) -> str:
    """Убирает мусор в начале, оставляет суть."""
    if not text or len(text) < 30:
        return text
    lines = text.strip().split("\n")
    # Первая строка короткая и похожа на обращение — пропускаем
    if len(lines) > 1 and len(lines[0].strip()) < 40 and ":" in lines[0]:
        return "\n".join(lines[1:]).strip() or text
    return text


def _extract_facts(text: str) -> list[dict]:
    """Извлекает проверяемые факты из высказывания через ИИ."""
    from ai.parsers import parse_json
    cleaned = _preprocess_for_factcheck(text)
    prompt = EXTRACT_FACTS_PROMPT.format(text=cleaned[:600])
    try:
        raw = _chat_with_fallback(prompt, max_tokens=800, temperature=0.15)
        data = parse_json(raw)
        if not isinstance(data, dict) or "facts" not in data:
            if raw:
                logger.info("Factcheck: модель вернула не JSON. raw=%s", raw[:300])
            return _extract_facts_fallback(cleaned)
        facts = data["facts"]
        if not isinstance(facts, list):
            return _extract_facts_fallback(cleaned)
        out = []
        for f in facts[:_MAX_FACTS]:
            if isinstance(f, dict) and f.get("claim") and f.get("queries"):
                out.append({
                    "claim": str(f["claim"])[:200],
                    "queries": [str(q)[:100] for q in (f.get("queries") or [])[:_MAX_QUERIES_PER_FACT]],
                })
        if not out:
            logger.info("Factcheck: модель вернула facts=[]. raw=%s", raw[:300])
            return _extract_facts_fallback(cleaned)
        return out
    except Exception as e:
        logger.warning("Factcheck extract_facts: %s", e)
        return _extract_facts_fallback(_preprocess_for_factcheck(text))


def _enrich_economic_queries(claim: str, queries: list[str]) -> list[str]:
    """Добавляет англ. GDP-запросы для экономических сравнений (Wikipedia, World Bank)."""
    t = claim.lower()
    if not any(tr in t for tr in _COMPARISON_TRIGGERS):
        return queries
    countries = [v for k, v in _COUNTRY_EN.items() if k in t]
    extra = []
    if len(countries) >= 2:
        extra.append(f"GDP {countries[0]} {countries[1]} comparison")
        extra.append(f"{countries[0]} vs {countries[1]} economy")
    elif len(countries) == 1:
        extra.append(f"GDP {countries[0]} economy")
    if not extra:
        return queries
    has_en = any(c in "abcdefghijklmnopqrstuvwxyz" for q in queries for c in q.lower())
    if not has_en:
        return list(queries) + extra
    return queries


def _verify_fact(claim: str, queries: list[str]) -> dict:
    """Проверяет один факт через поиск + ИИ."""
    from ai.parsers import parse_json
    queries = _enrich_economic_queries(claim, queries or [claim[:80]])
    snippets = []
    seen = set()
    for i, q in enumerate(queries[:_MAX_QUERIES_PER_FACT]):
        if i > 0:
            time.sleep(0.8)
        for r in _search_web(q, _MAX_SEARCH_RESULTS):
            key = (r.get("title", ""), r.get("body", "")[:200])
            if key in seen:
                continue
            seen.add(key)
            line = f"- {r.get('title', '')}: {r.get('body', '')[:300]}"
            if r.get("href"):
                line += f" [{r['href'][:60]}]"
            snippets.append(line)
    search_results = "\n".join(snippets[:25]) if snippets else "(нет результатов поиска)"
    prompt = VERIFY_FACT_PROMPT.format(claim=claim, search_results=search_results)
    try:
        raw = _chat_with_fallback(prompt, max_tokens=400, temperature=0.2)
        data = parse_json(raw)
        if isinstance(data, dict):
            return {
                "claim": claim,
                "verdict": data.get("verdict", "unclear"),
                "sources": data.get("sources") or [],
                "reasoning": data.get("reasoning", ""),
            }
    except Exception as e:
        logger.warning("Factcheck verify: %s", e)
    return {"claim": claim, "verdict": "unclear", "sources": [], "reasoning": "Ошибка проверки"}


def _build_summary(verifications: list[dict]) -> str:
    """Собирает итоговый связный текст."""
    block = "\n".join(
        f"Факт: {v['claim']}\nВердикт: {v['verdict']}. Источники: {', '.join(v.get('sources', []))}. {v.get('reasoning', '')}"
        for v in verifications
    )
    prompt = FINAL_SUMMARY_PROMPT.format(verifications=block)
    try:
        text = _chat_with_fallback(prompt, max_tokens=600, temperature=0.3)
        if text:
            return text[:800]
    except Exception as e:
        logger.warning("Factcheck summary: %s", e)
    # Fallback: простой текст
    lines = []
    for v in verifications:
        vd = {"confirmed": "подтверждено", "debunked": "опровергнуто", "unclear": "неясно"}.get(v["verdict"], "неясно")
        lines.append(f"• {v['claim']}: {vd}. {v.get('reasoning', '')}")
    return "Проверка: " + " ".join(lines)[:800]


def clear_factcheck_cache() -> int:
    """Очищает кэш факт-чека. Возвращает количество удалённых файлов."""
    if not _CACHE_DIR.exists():
        return 0
    count = 0
    for p in _CACHE_DIR.glob("*.json"):
        try:
            p.unlink()
            count += 1
        except OSError:
            pass
    return count


def run_factcheck(text: str, author_name: str = "") -> str:
    """
    Полный цикл факт-чека: извлечение фактов → поиск → верификация → итог.
    Возвращает связный текст с вердиктом и пруфами.
    """
    if not text or len(text.strip()) < 20:
        return ""
    text = text.strip()[:600]
    logger.info("Факт-чек: %s «%s»", author_name or "?", text[:80] + ("…" if len(text) > 80 else ""))
    ck = _cache_key(text)
    cached = _cache_get(ck)
    if cached:
        logger.info("Факт-чек cache hit: %s", ck)
        return cached
    facts = _extract_facts(text)
    if not facts:
        logger.info("Факт-чек: нет проверяемых фактов в тексте")
        return ""
    verifications = []
    for f in facts:
        v = _verify_fact(f["claim"], f.get("queries", []))
        verifications.append(v)
    result = _build_summary(verifications)
    if result:
        _cache_set(ck, result)
    return result
