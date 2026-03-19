"""
Генерация визуального портрета по психологическому описанию.

Провайдеры (порядок вызова):
1. HuggingFace Inference (HF_TOKEN) — бесплатно, FLUX на GPU HuggingFace
2. Gemini (GEMINI_API_KEY) — бесплатно, ~500/день
3. Replicate (REPLICATE_API_TOKEN) — 50 картинок/мес бесплатно
4. OpenRouter (OPENAI_API_KEY) — требует баланса

Локальная генерация через diffusers невозможна: полная модель FLUX.1-schnell
(bf16, ~32GB) не помещается в 32GB RAM при загрузке через from_pretrained.
"""

import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8-sig")
logger = logging.getLogger(__name__)

PORTRAIT_IMAGES_DIR = Path(__file__).resolve().parent.parent / "portrait_images"

PROVIDERS = ("huggingface", "gemini", "replicate", "openrouter")

REPLICATE_FLUX_VERSION = "fb8af171cfa1616ddcf1242c093f9c46bcada5ad4cf6f2fbe8b81b330ec5c003"


_VISUAL_PROMPT_SYSTEM = """You are a prompt engineer for an AI image generator (FLUX).
Your task: convert a psychological portrait into a concise visual description for generating a portrait photograph.

Rules:
- Output ONLY the image prompt, nothing else
- Max 150 words
- Focus on: appearance, facial features, expression, gaze, posture, clothing style, lighting, mood
- Infer visual traits from personality (e.g. aggressive → sharp jawline, tense expression; intellectual → glasses, thoughtful gaze)
- Include unique distinguishing details that make this person different from others
- Use English, comma-separated descriptors
- Start with "Professional portrait photograph:"
"""

_OCEAN_VISUAL_MAP = {
    "openness": {
        "high": "creative artistic look, unconventional style, curious bright eyes, eclectic accessories",
        "low": "conventional neat appearance, traditional clothing, composed steady gaze",
    },
    "conscientiousness": {
        "high": "immaculate grooming, crisp structured clothing, precise posture, organized appearance",
        "low": "relaxed casual style, slightly disheveled, laid-back posture",
    },
    "extraversion": {
        "high": "warm broad smile, open expressive face, vibrant clothing colors, dynamic energetic pose",
        "low": "reserved subtle expression, muted tones, contemplative inward gaze, calm quiet demeanor",
    },
    "agreeableness": {
        "high": "gentle kind eyes, soft warm smile, approachable open posture, warm lighting",
        "low": "sharp analytical gaze, firm set jaw, confident assertive posture, cool lighting",
    },
    "neuroticism": {
        "high": "intense slightly worried eyes, tension in brow, restless energy, dramatic moody lighting",
        "low": "calm serene expression, relaxed shoulders, steady confident gaze, even soft lighting",
    },
}

_DARK_TRIAD_VISUAL = {
    "narcissism": "self-assured commanding presence, perfectly styled, slightly elevated chin",
    "machiavellianism": "calculating piercing gaze, subtle knowing half-smile, sharp attire",
    "psychopathy": "flat affect, unnervingly calm expression, cold detached eyes",
}


def _build_ocean_visual_hints(profile_data: dict | None) -> str:
    """Build visual hints from OCEAN and Dark Triad scores."""
    if not profile_data:
        return ""
    parts = []
    ocean = profile_data.get("ocean") or {}
    for dim, mapping in _OCEAN_VISUAL_MAP.items():
        score = ocean.get(dim)
        if score is not None:
            level = "high" if float(score) >= 0.6 else "low" if float(score) <= 0.4 else None
            if level:
                parts.append(mapping[level])
    dt = profile_data.get("dark_triad") or {}
    for trait, hint in _DARK_TRIAD_VISUAL.items():
        info = dt.get(trait) or {}
        score = info.get("score", 0) if isinstance(info, dict) else 0
        if float(score) >= 0.6:
            parts.append(hint)
    topics = profile_data.get("topics") or {}
    primary = topics.get("primary") or []
    if primary:
        topic_hints = {
            "technical": "tech-savvy appearance, modern minimal style",
            "politics": "authoritative formal look, power dressing",
            "humor": "playful mischievous expression, laugh lines",
            "conflict": "intense confrontational energy, sharp features",
            "personal": "warm intimate presence, genuine authentic look",
        }
        for t in primary[:2]:
            if t in topic_hints:
                parts.append(topic_hints[t])
    return ", ".join(parts)


def _portrait_to_visual_prompt(portrait_text: str, display_name: str = "", personality_profile: dict | None = None) -> str:
    """Извлекает визуальные ключевые слова через LLM с учётом OCEAN-профиля."""
    full_text = (portrait_text or "").strip()
    if not full_text:
        return "Professional portrait photograph: thoughtful person, neutral expression, soft lighting"

    ocean_hints = _build_ocean_visual_hints(personality_profile)

    user_msg = full_text
    if display_name:
        user_msg = f"Name: {display_name}\n\n{user_msg}"
    if ocean_hints:
        user_msg += f"\n\nPersonality visual cues (use these for the portrait): {ocean_hints}"

    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if api_key:
        try:
            payload = {
                "contents": [{"parts": [
                    {"text": _VISUAL_PROMPT_SYSTEM + "\n\n" + user_msg},
                ]}],
                "generationConfig": {"maxOutputTokens": 300, "temperature": 0.7},
            }
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent?key={api_key}"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            result = ""
            for part in (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or []:
                result += part.get("text", "")
            result = result.strip()
            if result and len(result) > 30:
                if not result.startswith("Professional"):
                    result = "Professional portrait photograph: " + result
                logger.info("Визуальный промпт (Gemini, %d→%d символов): %s",
                            len(full_text), len(result), result[:100])
                return result
        except Exception as e:
            logger.warning("Gemini prompt extraction failed: %s", e)

    return _portrait_to_prompt_simple(full_text, display_name)


def _portrait_to_prompt_simple(portrait_text: str, display_name: str = "") -> str:
    """Простая очистка — полный текст без LLM."""
    text = (portrait_text or "").strip()
    if not text:
        return "Professional portrait photograph: thoughtful person, neutral expression, soft lighting"
    if display_name:
        text = f"{display_name}. {text}"
    text = re.sub(r"##\s*[^\n]+", "\n", text)
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return f"Professional portrait photograph: {text}"


def _generate_image_huggingface(prompt: str) -> bytes | None:
    """HuggingFace Inference API — бесплатно. GPU на стороне HF."""
    token = (os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN") or "").strip()
    if not token:
        return None
    try:
        url = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
        payload = json.dumps({"inputs": prompt}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        if len(data) < 1000:
            logger.warning("HuggingFace: ответ слишком маленький (%d байт), возможно ошибка", len(data))
            return None
        return data
    except Exception as e:
        logger.warning("HuggingFace: %s", e)
        return None


def _generate_image_gemini(prompt: str) -> bytes | None:
    """Google AI Studio — бесплатно, региональные ограничения."""
    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        return None
    try:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "1:1"}},
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent?key={api_key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode())
        for part in (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or []:
            if "inlineData" in part:
                b64 = part["inlineData"].get("data")
                if b64:
                    return base64.b64decode(b64)
        return None
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code in (403, 451):
            logger.info("Gemini недоступен в регионе")
        elif e.code == 429:
            logger.warning("Gemini: лимит исчерпан (429)")
        else:
            logger.warning("Gemini: HTTP %s %s", e.code, body[:200])
        return None
    except Exception as e:
        logger.warning("Gemini: %s", e)
        return None


def _generate_image_replicate(prompt: str) -> bytes | None:
    """Replicate — 50 картинок/мес бесплатно."""
    token = (os.getenv("REPLICATE_API_TOKEN") or "").strip()
    if not token:
        return None
    try:
        payload = {
            "version": REPLICATE_FLUX_VERSION,
            "input": {"prompt": prompt},
        }
        req = urllib.request.Request(
            "https://api.replicate.com/v1/predictions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Prefer": "wait=60",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
        output = data.get("output")
        if not output:
            return None
        img_url = output if isinstance(output, str) else (output[0] if isinstance(output, list) and output else None)
        if not img_url or not str(img_url).startswith("http"):
            return None
        with urllib.request.urlopen(img_url, timeout=60) as img_resp:
            return img_resp.read()
    except Exception as e:
        logger.warning("Replicate: %s", e)
        return None


def _generate_image_openrouter(prompt: str) -> bytes | None:
    """OpenRouter — требует пополнения баланса."""
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    models = [
        ("bytedance-seed/seedream-4.5", ["image"]),
        ("google/gemini-2.5-flash-image", ["image", "text"]),
        ("black-forest-labs/flux.2-klein-4b", ["image"]),
    ]
    for model, modalities in models:
        try:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "modalities": modalities,
            }
            if "text" in modalities:
                payload["image_config"] = {"aspect_ratio": "1:1"}
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode())
            images = (data.get("choices") or [{}])[0].get("message", {}).get("images") or []
            if not images:
                continue
            url_val = images[0].get("image_url") or images[0].get("imageUrl") or {}
            url_str = url_val.get("url", "") if isinstance(url_val, dict) else ""
            if url_str.startswith("data:"):
                return base64.b64decode(url_str.split(",", 1)[-1])
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5)
            logger.warning("OpenRouter %s: HTTP %s", model, e.code)
            continue
        except Exception as e:
            logger.warning("OpenRouter %s: %s", model, e)
            continue
    return None


def clear_portrait_model_cache() -> bool:
    """Заглушка для совместимости (модель больше не кешируется в RAM)."""
    return False


def generate_portrait_image(
    user_id: int,
    portrait_text: str,
    display_name: str = "",
    provider: str | None = None,
    personality_profile: dict | None = None,
) -> Path | None:
    if not (portrait_text or "").strip():
        logger.warning("Пустой портрет для user_id=%s", user_id)
        return None

    visual_prompt = _portrait_to_visual_prompt(portrait_text, display_name, personality_profile)
    logger.info("Промпт из портрета: %s", visual_prompt[:150])

    img_data = None
    used_provider = None

    def try_hf():
        d = _generate_image_huggingface(visual_prompt)
        return d, "HuggingFace" if d else None

    def try_gem():
        d = _generate_image_gemini(visual_prompt)
        return d, "Gemini" if d else None

    def try_rep():
        d = _generate_image_replicate(visual_prompt)
        return d, "Replicate" if d else None

    def try_or():
        d = _generate_image_openrouter(visual_prompt)
        return d, "OpenRouter" if d else None

    order = {
        "huggingface": [try_hf],
        "gemini": [try_gem],
        "replicate": [try_rep],
        "openrouter": [try_or],
    }

    if provider and provider.lower() in order:
        logger.info("Провайдер: %s", provider)
        for fn in order[provider.lower()]:
            img_data, used_provider = fn()
            break
    else:
        for fn in [try_hf, try_gem, try_rep, try_or]:
            img_data, used_provider = fn()
            if img_data:
                break

    if not img_data:
        logger.warning(
            "Генерация не удалась. Проверьте: HF_TOKEN, GEMINI_API_KEY, REPLICATE_API_TOKEN или OpenRouter."
        )
        return None

    PORTRAIT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    path = PORTRAIT_IMAGES_DIR / f"{user_id}.png"
    path.write_bytes(img_data)
    logger.info("Портрет сохранён: %s (провайдер: %s)", path, used_provider or "?")
    return path
