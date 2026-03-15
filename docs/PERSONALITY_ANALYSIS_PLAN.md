# nopolicybot — план агента: анализ личности и сравнение пользователей

## Назначение файла

Инструкция для AI-агента по расширению функционала психологического анализа пользователей. Цель — перейти от свободного текстового портрета к структурированной, сравнимой, динамической системе профилирования на основе академических моделей личности.

---

## Соглашения

- `[ПРОВЕРИТЬ]` — прочитать текущий код перед действием
- `[ВЫПОЛНИТЬ]` — конкретная реализация
- `[ПРОМПТ]` — текст промпта который нужно использовать или адаптировать
- `[ТЕСТ]` — проверка результата
- `[GATE]` — блокирующая проверка перед следующим этапом

---

## Этап P-1: Структурированная схема личности

**Цель:** заменить свободный текстовый портрет на JSON-схему с числовыми измерениями.

### Модели

- **Big Five (OCEAN):** openness, conscientiousness, extraversion, agreeableness, neuroticism — float 0.0–1.0
- **Dark Triad:** narcissism, machiavellianism, psychopathy — low/medium/high + score 0.0–1.0
- **Коммуникативный профиль:** style, conflict_tendency, influence_seeking, emotional_expressiveness, topic_consistency

### Целевая JSON-схема

```json
{
  "user_id": "...",
  "username": "...",
  "generated_at": "2025-01-01T00:00:00Z",
  "period_days": 30,
  "messages_analyzed": 347,
  "confidence": 0.78,
  "ocean": { "openness": 0.72, "conscientiousness": 0.45, "extraversion": 0.81, "agreeableness": 0.38, "neuroticism": 0.61 },
  "dark_triad": {
    "narcissism": {"label": "low", "score": 0.21},
    "machiavellianism": {"label": "medium", "score": 0.48},
    "psychopathy": {"label": "low", "score": 0.15}
  },
  "communication": { "style": "assertive", "conflict_tendency": 0.65, "influence_seeking": 0.55, "emotional_expressiveness": 0.70, "topic_consistency": 0.40 },
  "emotional_profile": { "valence": 0.42, "arousal": 0.68, "dominant_emotions": ["раздражение", "энтузиазм"] },
  "topics": { "primary": ["политика"], "secondary": ["спорт"], "avoided": ["личное"] },
  "role_in_community": "provocateur",
  "summary": "Краткое резюме 2–3 предложения"
}
```

### Реализация

- `services/personality/schema.py` — Pydantic-модель PersonalityProfile
- Таблица `personality_profiles` в DB
- Обновить промпт и сохранение

---

## Этап P-2: Промпт для структурированного портрета

**Системный промпт** (`profile_system.txt`): эксперт-психолог, OCEAN + Dark Triad + communication, JSON only, confidence по количеству сообщений.

**Пользовательский промпт** (`profile_user.txt`): `{messages_count}`, `{username}`, `{user_id}`, `{period_days}`, `{chat_description}`, `{messages_text}`, `{schema_json}`.

Парсинг: `json.loads()`, валидация Pydantic, retry при ошибке (max 2).

---

## Этап P-3: Ансамбль моделей и confidence

- `services/personality/ensemble.py` — N моделей параллельно, среднее по OCEAN, std, agreement_score
- Поле `ensemble_stats` в схеме

---

## Этап P-4: Динамический портрет — история и дрейф

- Автообновление каждые 7 дней
- `services/personality/drift.py` — calculate_drift, PersonalityDrift, alert в at-risk

---

## Этап P-5: Контекстные профили по топикам

- `services/personality/contextual.py` — разбивка по топикам, мини-профили
- `context_profiles` в JSON

---

## Этап P-6: Сравнение пользователей

- `services/personality/comparison.py` — compare_two, cluster_community
- API: `/api/v2/personality/{user_id}`, `/history`, `/drift`, `/compare`, `/community/{chat_id}/clusters`

---

## Этап P-7: UI — визуализация

- Radar chart OCEAN, Dark Triad bars, Timeline drift, Карточка сравнения, Карта кластеров

---

## Этап P-8: Интеграция с decision engine

- personality_context в decision payload, промпт, audit log

---

## Этап P-9: Поведенческая верификация

- `services/personality/verification.py` — correlation профиль vs поведение, reliability badges

---

## Зависимости

```
P-1 → P-2, P-4, P-6, P-8
P-2 → P-3, P-5
P-1 + P-2 → P-6
P-6 → P-7
P-1 + P-8 → P-9
```

---

## Важные ограничения

- Не клиническая диагностика — «коммуникативный профиль», не «диагноз»
- Confidence < 0.5 не использовать для автоматических решений
- Хранить историю, не перезаписывать

---

## Модели OpenRouter (бесплатные)

- **Ансамбль P-3:** `meta-llama/llama-3.3-70b-instruct:free`, `arcee-ai/trinity-large-preview:free`, `stepfun/step-3.5-flash:free`
- **Быстрая классификация:** `arcee-ai/trinity-mini:free`, `z-ai/glm-4.5-air:free`
- **Батч:** `nvidia/nemotron-3-nano-30b-a3b:free`
- **Summary:** `meta-llama/llama-3.3-70b-instruct:free`
