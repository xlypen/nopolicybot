# Точки взаимодействия с ИИ в проекте

Все места, где вызываются OpenRouter / Gemini / OpenAI-совместимый API.

---

## 1. `ai/client.py` — центральный слой

| Функция | Назначение | Fallback |
|--------|------------|----------|
| `get_client()` | OpenAI-клиент (OpenRouter). Требует `OPENAI_API_KEY`. | — |
| `gemini_chat_complete()` | Прямой вызов Gemini REST API. | Нет (возвращает `None` при ошибке) |
| `gemini_analyze_image()` | Анализ изображения через Gemini Vision. | Нет |
| `chat_complete_with_fallback()` | Универсальный вызов: при `prefer_free` — Gemini, иначе OpenRouter → бесплатные модели → Gemini. | Да (цепочка) |
| `prefer_free_mode()` | Решает, идти ли сначала в Gemini. Зависит от `AI_PREFER_FREE`, `AI_USE_GEMINI_FIRST`, `AI_USE_OPENROUTER_FIRST`, наличия ключей. | — |

---

## 2. `ai_analyzer.py` — анализ и ответы бота

| Функция | Вызов ИИ | Fallback / примечание |
|--------|----------|------------------------|
| **analyze_messages** | `chat_complete_with_fallback(prefer_free=prefer_free_mode())` | Да, повтор + дефолт |
| **analyze_close_attention** | `chat_complete_with_fallback(..., prefer_free=prefer_free_mode())` | Да |
| **analyze_image** | При `prefer_free_mode()` — `gemini_analyze_image()`, иначе `get_client()` + vision-модели | При prefer_free — дефолт при пустом ответе; иначе OpenRouter + бесплатные |
| **analyze_message_for_reply** | `chat_complete_with_fallback(..., prefer_free=prefer_free_mode())` | Да |
| **should_pause_dialog** | `chat_complete_with_fallback(..., prefer_free=prefer_free_mode())` | Да |
| **analyze_batch_style** | `get_client()` + `client.chat.completions.create` | Нет (может бросить при отсутствии ключа) |
| **update_user_portrait** | `get_client()` + create | При 402 — `gemini_chat_complete`; иначе может бросить |
| **_batch_style_from_cache_or_api** (build_deep_portrait) | `get_client()` + create | При 402 — `gemini_chat_complete` |
| **assess_tone_toward_bot** | `get_client()` + create | При 402 — `gemini_chat_complete` |
| **_generate_reply_ensemble** | При `prefer_free_mode()` — `chat_complete_with_fallback(prefer_free=True)`, при пустом — `prefer_free=False`; иначе `get_client()` в `_generate_with_model` | При 402 в _generate_with_model — `gemini_chat_complete`; в конце ансамбля — ещё раз Gemini |
| **_select_best_candidate** | `get_client()` + create (выбор лучшего ответа из кандидатов) | Нет |
| **generate_substantive_reply** | `get_client()` в цепочке вызовов | Через _generate_reply_ensemble при prefer_free |
| **generate_technical_reply** | то же | то же |
| **evaluate_question_of_day_reply** | `get_client()` + create | Нет |
| **generate_engaging_reply_to_question_of_day** | `get_client()` + create | Нет |

---

## 3. `bot.py`

| Место | Вызов ИИ | Fallback |
|-------|----------|----------|
| **_last_resort_gemini_reply** | `gemini_chat_complete()` | При ошибке — текст из `reply_fallback_on_error` |
| Остальная логика ответа в личку | Через `analyze_message_for_reply`, `should_pause_dialog`, `generate_*` из ai_analyzer | см. выше |

---

## 4. `voice_transcribe.py`

| Функция | Вызов ИИ | Fallback |
|--------|----------|----------|
| **transcribe_with_openrouter** | `get_client()` + `client.chat.completions.create` (audio) | При 402 перебор моделей; при полном фейле — пустая строка (не бросает) |
| **transcribe_voice** | Whisper локально или `transcribe_with_openrouter` | При отсутствии ключа/ошибке OpenRouter возвращает `""` |

---

## 5. `services/personality/`

| Файл / функция | Вызов ИИ | Fallback |
|----------------|----------|----------|
| **contextual.py** — `detect_topics_llm` | `get_client()` + create | При ошибке — keyword fallback по батчу |
| **auto_build.py** | `get_client()` + create | Нет |
| **builder.py** | `get_client()` + create | Нет |
| **image_generator.py** | Свой HTTP на OpenRouter / другие провайдеры (Gemini, HF и т.д.) | По конфигу провайдеров |

---

## 6. `services/factcheck.py`

| Функция | Вызов ИИ | Fallback |
|--------|----------|----------|
| **_chat_with_fallback** | `get_client()` + перебор `get_chat_models_for_fallback()` | При 402 — следующая модель; в конце может пробросить исключение |

---

## 7. `services/chat_analysis.py`

| Место | Вызов ИИ | Fallback |
|-------|----------|----------|
| Анализ чата | `get_client()` + create | Нет |

---

## 8. `social_graph.py`

| Место | Вызов ИИ | Fallback |
|-------|----------|----------|
| Генерация контекста графа (внутри функции) | `get_client()` + create | Нет |

---

## 9. Генерация изображений (портреты)

| Файл | Провайдеры | Fallback |
|------|------------|----------|
| **services/personality/image_generator.py** | HF, Gemini, Replicate, OpenRouter по конфигу | Перебор провайдеров |
| **services/portrait_image.py** | HF, Gemini, Replicate, OpenRouter | Перебор, свои HTTP-вызовы |

---

## Итог: где может падать при отсутствии OPENAI_API_KEY или 402

- **analyze_batch_style** — только `get_client()`.
- **update_user_portrait** — get_client, при 402 есть Gemini.
- **_select_best_candidate** — только get_client.
- **evaluate_question_of_day_reply** / **generate_engaging_reply_to_question_of_day** — только get_client.
- **services/personality/contextual.py** — get_client; при ошибке — keyword fallback.
- **services/personality/auto_build.py**, **builder.py** — только get_client.
- **services/factcheck.py** — get_client, при 402 перебор моделей, в конце может пробросить.
- **services/chat_analysis.py** — только get_client.
- **social_graph.py** — только get_client.
- **voice_transcribe** — при отсутствии ключа get_client() кинет; обёртка может поймать и вернуть "".

Рекомендация: для единообразия и устойчивости к 402/отсутствию ключа в перечисленных местах по возможности использовать `chat_complete_with_fallback(..., prefer_free=prefer_free_mode())` вместо прямого `get_client()` + `create`.
