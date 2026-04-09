"""Model config for personality analysis (P-3)."""

import os

# Ансамбль по умолчанию — платные слоты OpenRouter (без :free), чтобы при наличии баланса
# сборка OCEAN не упиралась в лимиты/доступность free. Только бесплатный режим —
# задайте PERSONALITY_ENSEMBLE_MODELS с суффиксом :free.
ENSEMBLE_PRIMARY = [
    "meta-llama/llama-3.3-70b-instruct",
    "openai/gpt-4o-mini",
    "deepseek/deepseek-chat",
]

# Резервный порядок (для будущих вызовов / документации)
FALLBACK_ORDER = [
    "meta-llama/llama-3.3-70b-instruct",
    "openai/gpt-4o-mini",
    "deepseek/deepseek-chat",
    "meta-llama/llama-3.3-70b-instruct:free",
    "stepfun/step-3.5-flash:free",
]

# Override from env (comma-separated)
def get_ensemble_models() -> list[str]:
    raw = (os.getenv("PERSONALITY_ENSEMBLE_MODELS") or "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    return ENSEMBLE_PRIMARY.copy()
