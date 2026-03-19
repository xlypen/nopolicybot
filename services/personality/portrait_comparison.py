"""IMG-5: Сравнительный диптих — два портрета рядом."""

from __future__ import annotations

import io
import struct
import zlib
from typing import TYPE_CHECKING

from services.personality.image_prompt_builder import build_image_prompt
from services.personality.image_generator import generate_image_from_prompt

if TYPE_CHECKING:
    from services.personality.schema import PersonalityProfile


def _png_dimensions(data: bytes) -> tuple[int, int]:
    """Extract width, height from PNG header."""
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        return 1024, 1024
    w, h = struct.unpack('>II', data[16:24])
    return w, h


def _simple_hconcat(img_a: bytes, img_b: bytes, gap: int = 4) -> bytes:
    """
    Minimal PNG horizontal concat without PIL dependency.
    Both images must be same height. Falls back to img_a if sizes mismatch.
    """
    try:
        from PIL import Image
        a = Image.open(io.BytesIO(img_a))
        b = Image.open(io.BytesIO(img_b))
        h = min(a.height, b.height)
        if a.height != h:
            a = a.resize((int(a.width * h / a.height), h))
        if b.height != h:
            b = b.resize((int(b.width * h / b.height), h))
        total_w = a.width + b.width + gap
        diptych = Image.new("RGB", (total_w, h), (30, 30, 30))
        diptych.paste(a, (0, 0))
        diptych.paste(b, (a.width + gap, 0))
        buf = io.BytesIO()
        diptych.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        return img_a


def generate_comparison_diptych(
    profile_a: "PersonalityProfile",
    profile_b: "PersonalityProfile",
    *,
    style_variant: str = "concept_art",
) -> dict | None:
    """
    Generates two portraits and combines them into a horizontal diptych.

    Returns dict:
      image_bytes: bytes
      model_a / model_b: str
      prompt_a / prompt_b: str
      generation_time_sec: float
    or None if generation failed.
    """
    prompt_a = build_image_prompt(profile_a, style_variant=style_variant)
    prompt_b = build_image_prompt(profile_b, style_variant=style_variant)

    result_a = generate_image_from_prompt(
        prompt_a["positive_prompt"], prompt_a["negative_prompt"]
    )
    if not result_a:
        return None

    result_b = generate_image_from_prompt(
        prompt_b["positive_prompt"], prompt_b["negative_prompt"]
    )
    if not result_b:
        return None

    diptych_bytes = _simple_hconcat(result_a["image_bytes"], result_b["image_bytes"])

    total_time = result_a["generation_time_sec"] + result_b["generation_time_sec"]

    return {
        "image_bytes": diptych_bytes,
        "model_a": result_a["model_used"],
        "model_b": result_b["model_used"],
        "prompt_a": prompt_a["positive_prompt"],
        "prompt_b": prompt_b["positive_prompt"],
        "seed_a": prompt_a["seed_description"],
        "seed_b": prompt_b["seed_description"],
        "generation_time_sec": round(total_time, 2),
    }
