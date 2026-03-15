"""Model config for personality analysis (P-3)."""

import os

# Primary ensemble for full OCEAN profile
ENSEMBLE_PRIMARY = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "arcee-ai/trinity-large-preview:free",
    "stepfun/step-3.5-flash:free",
]

# Fallback when primary models unavailable
FALLBACK_ORDER = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "stepfun/step-3.5-flash:free",
    "arcee-ai/trinity-large-preview:free",
    "arcee-ai/trinity-mini:free",
]

# Override from env (comma-separated)
def get_ensemble_models() -> list[str]:
    raw = (os.getenv("PERSONALITY_ENSEMBLE_MODELS") or "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    return ENSEMBLE_PRIMARY.copy()
