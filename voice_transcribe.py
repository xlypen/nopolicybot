"""
Транскрипция голосовых сообщений: локальный Whisper или OpenRouter.
Telegram присылает voice в формате OGG/Opus.
"""

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_WHISPER_MODEL = None


def _get_whisper_model():
    """Ленивая загрузка модели faster-whisper."""
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        try:
            from faster_whisper import WhisperModel
            model_size = os.getenv("WHISPER_MODEL_SIZE", "base")
            device = os.getenv("WHISPER_DEVICE", "cpu")
            compute_type = "int8" if device == "cpu" else "float16"
            _WHISPER_MODEL = WhisperModel(model_size, device=device, compute_type=compute_type)
            logger.info("Whisper модель загружена: %s (%s)", model_size, device)
        except ImportError:
            logger.warning("faster-whisper не установлен. pip install faster-whisper")
            raise
    return _WHISPER_MODEL


def transcribe_with_whisper(audio_bytes: bytes, mime: str = "audio/ogg") -> str:
    """
    Транскрипция через локальный faster-whisper.
    Поддерживает OGG (Telegram voice), WAV, MP3 и др.
    """
    if not audio_bytes or len(audio_bytes) < 100:
        return ""
    suffix = ".ogg" if "ogg" in mime.lower() or mime == "audio/opus" else ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        try:
            f.write(audio_bytes)
            f.flush()
            path = f.name
        except Exception:
            return ""
    try:
        model = _get_whisper_model()
        segments, info = model.transcribe(path, language="ru", beam_size=1, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments if s.text).strip()
        return text
    except Exception as e:
        logger.warning("Ошибка Whisper: %s", e)
        return ""
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


# Бесплатные модели с поддержкой audio (OpenRouter)
AUDIO_FREE_MODELS = [
    "google/gemini-2.0-flash-exp:free",
    "google/gemini-2.5-flash-preview-05-20:free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
]


def transcribe_with_openrouter(audio_bytes: bytes, mime: str = "audio/ogg") -> str:
    """
    Транскрипция через OpenRouter (модели с audio input). При 402 пробует бесплатные модели.
    """
    if not audio_bytes or len(audio_bytes) < 100:
        return ""
    import base64
    from ai.client import get_client, _is_402
    b64 = base64.standard_b64encode(audio_bytes).decode("ascii")
    fmt = "ogg" if "ogg" in mime.lower() or "opus" in mime.lower() else "wav"
    client = get_client()
    env_models = [m.strip() for m in (os.getenv("OPENROUTER_AUDIO_MODELS") or "").split(",") if m.strip()]
    models = env_models or AUDIO_FREE_MODELS
    for model_id in models:
        try:
            resp = client.chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "user", "content": [
                        {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}},
                        {"type": "text", "text": "Транскрибируй это голосовое сообщение на русском. Верни только текст, без пояснений."},
                    ]},
                ],
                max_tokens=500,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                return text
        except Exception as e:
            if _is_402(e):
                logger.info("Голос: модель %s — 402, пробуем следующую", model_id)
            else:
                logger.debug("OpenRouter %s: %s", model_id, e)
            continue
    return ""


def transcribe_voice(audio_bytes: bytes, mime: str = "audio/ogg") -> str:
    """
    Транскрибирует голосовое сообщение. Сначала пробует локальный Whisper,
    при недоступности или ошибке — OpenRouter (если настроен).
    """
    method = (os.getenv("VOICE_TRANSCRIPTION") or "whisper").strip().lower()

    def _try_whisper() -> str:
        try:
            return transcribe_with_whisper(audio_bytes, mime)
        except Exception as e:
            logger.warning("Whisper недоступен (%s: %s), пробуем OpenRouter", type(e).__name__, e)
            return ""

    def _try_openrouter() -> str:
        try:
            return transcribe_with_openrouter(audio_bytes, mime)
        except Exception as e:
            logger.debug("OpenRouter аудио: %s", e)
            return ""

    if method == "openrouter":
        text = _try_openrouter()
        if text:
            return text
        return _try_whisper()

    text = _try_whisper()
    if text:
        return text
    return _try_openrouter()
