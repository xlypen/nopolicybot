"""
IMG-2: Генерация изображений по профилю личности с fallback по моделям.

Провайдеры (в порядке приоритета):
1. HuggingFace FLUX.1-schnell — ~100 req/day, Apache 2.0
2. HuggingFace FLUX.1-dev — ~50 req/day, более детальная
3. HuggingFace SDXL — ~200 req/day, запасная
4. Gemini 2.5 Flash Image — ~500 req/day
5. Replicate FLUX — ~50/мес бесплатно
6. OpenRouter — требует баланса
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

REPLICATE_FLUX_VERSION = "fb8af171cfa1616ddcf1242c093f9c46bcada5ad4cf6f2fbe8b81b330ec5c003"

IMAGE_MODEL_PRIORITY: list[dict] = [
    {
        "name": "flux-schnell",
        "provider": "huggingface",
        "model_id": "black-forest-labs/FLUX.1-schnell",
        "api_key_env": "HF_TOKEN",
        "alt_key_env": "HUGGINGFACE_TOKEN",
        "daily_limit": 100,
        "avg_seconds": 8,
        "params": {"num_inference_steps": 4, "guidance_scale": 0.0},
    },
    {
        "name": "flux-dev",
        "provider": "huggingface",
        "model_id": "black-forest-labs/FLUX.1-dev",
        "api_key_env": "HF_TOKEN",
        "alt_key_env": "HUGGINGFACE_TOKEN",
        "daily_limit": 50,
        "avg_seconds": 25,
        "params": {"num_inference_steps": 30, "guidance_scale": 3.5},
    },
    {
        "name": "sdxl",
        "provider": "huggingface",
        "model_id": "stabilityai/stable-diffusion-xl-base-1.0",
        "api_key_env": "HF_TOKEN",
        "alt_key_env": "HUGGINGFACE_TOKEN",
        "daily_limit": 200,
        "avg_seconds": 15,
        "params": {"num_inference_steps": 30, "guidance_scale": 7.5},
    },
    {
        "name": "gemini-flash-image",
        "provider": "gemini",
        "model_id": "gemini-2.5-flash-image",
        "api_key_env": "GEMINI_API_KEY",
        "alt_key_env": "GOOGLE_API_KEY",
        "daily_limit": 500,
        "avg_seconds": 12,
    },
    {
        "name": "replicate-flux",
        "provider": "replicate",
        "model_id": "flux",
        "api_key_env": "REPLICATE_API_TOKEN",
        "daily_limit": 50,
        "avg_seconds": 20,
    },
    {
        "name": "openrouter",
        "provider": "openrouter",
        "model_id": "multiple",
        "api_key_env": "OPENAI_API_KEY",
        "daily_limit": 999,
        "avg_seconds": 15,
    },
]


def _get_api_key(cfg: dict) -> str:
    key = (os.getenv(cfg["api_key_env"]) or "").strip()
    if not key:
        alt = cfg.get("alt_key_env")
        if alt:
            key = (os.getenv(alt) or "").strip()
    return key


def _generate_huggingface(prompt: str, negative_prompt: str, cfg: dict) -> bytes | None:
    token = _get_api_key(cfg)
    if not token:
        return None
    model_id = cfg["model_id"]
    try:
        url = f"https://router.huggingface.co/hf-inference/models/{model_id}"
        payload = {"inputs": prompt}
        if negative_prompt and "sdxl" in model_id.lower():
            payload["parameters"] = {"negative_prompt": negative_prompt}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        if len(data) < 1000:
            logger.warning("HuggingFace %s: response too small (%d bytes)", model_id, len(data))
            return None
        return data
    except urllib.error.HTTPError as e:
        if e.code == 429:
            logger.info("HuggingFace %s: rate limit (429)", model_id)
        elif e.code == 503:
            logger.info("HuggingFace %s: model loading (503)", model_id)
        else:
            logger.warning("HuggingFace %s: HTTP %s", model_id, e.code)
        return None
    except Exception as e:
        logger.warning("HuggingFace %s: %s", model_id, e)
        return None


def _generate_gemini(prompt: str, cfg: dict) -> bytes | None:
    api_key = _get_api_key(cfg)
    if not api_key:
        return None
    try:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": "1:1"},
            },
        }
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.5-flash-image:generateContent?key={api_key}"
        )
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
        if e.code in (403, 451):
            logger.info("Gemini: region blocked (%s)", e.code)
        elif e.code == 429:
            logger.info("Gemini: rate limit (429)")
        else:
            logger.warning("Gemini: HTTP %s", e.code)
        return None
    except Exception as e:
        logger.warning("Gemini: %s", e)
        return None


def _generate_replicate(prompt: str, cfg: dict) -> bytes | None:
    token = _get_api_key(cfg)
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


def _generate_openrouter(prompt: str, cfg: dict) -> bytes | None:
    api_key = _get_api_key(cfg)
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
                time.sleep(3)
            logger.warning("OpenRouter %s: HTTP %s", model, e.code)
            continue
        except Exception as e:
            logger.warning("OpenRouter %s: %s", model, e)
            continue
    return None


_PROVIDER_FN = {
    "huggingface": _generate_huggingface,
    "gemini": _generate_gemini,
    "replicate": _generate_replicate,
    "openrouter": _generate_openrouter,
}


def generate_image_from_prompt(
    positive_prompt: str,
    negative_prompt: str = "",
    *,
    preferred_provider: str | None = None,
) -> dict | None:
    """
    Генерирует изображение, перебирая модели по приоритету.

    Returns dict:
      image_bytes: bytes
      model_used: str
      provider: str
      generation_time_sec: float
    или None если все модели не сработали.
    """
    last_error: Exception | None = None

    if preferred_provider:
        candidates = [
            c for c in IMAGE_MODEL_PRIORITY
            if c["provider"] == preferred_provider or c["name"] == preferred_provider
        ]
        candidates += [c for c in IMAGE_MODEL_PRIORITY if c not in candidates]
    else:
        candidates = list(IMAGE_MODEL_PRIORITY)

    for cfg in candidates:
        if not _get_api_key(cfg):
            continue
        provider = cfg["provider"]
        fn = _PROVIDER_FN.get(provider)
        if not fn:
            continue
        try:
            start = time.time()
            if provider == "huggingface":
                img_bytes = fn(positive_prompt, negative_prompt, cfg)
            elif provider == "gemini":
                img_bytes = fn(positive_prompt, cfg)
            elif provider == "replicate":
                img_bytes = fn(positive_prompt, cfg)
            elif provider == "openrouter":
                img_bytes = fn(positive_prompt, cfg)
            else:
                continue

            elapsed = time.time() - start

            if img_bytes and len(img_bytes) > 1000:
                logger.info(
                    "Image generated: model=%s provider=%s time=%.1fs size=%d",
                    cfg["name"], provider, elapsed, len(img_bytes),
                )
                return {
                    "image_bytes": img_bytes,
                    "model_used": cfg["name"],
                    "provider": provider,
                    "generation_time_sec": round(elapsed, 2),
                }
        except Exception as e:
            last_error = e
            logger.warning("Model %s failed: %s", cfg["name"], e)
            continue

    if last_error:
        logger.error("All image models failed. Last error: %s", last_error)
    else:
        logger.error("No API keys configured for image generation")
    return None
