"""Tone analysis with lightweight fallback."""

from __future__ import annotations


def _heuristic_score(text: str) -> float:
    t = (text or "").lower()
    pos = sum(1 for w in ("хорош", "отлич", "класс", "спасибо", "люблю") if w in t)
    neg = sum(1 for w in ("плох", "ненав", "ужас", "туп", "идиот", "хуй") if w in t)
    if pos == neg == 0:
        return 0.0
    score = (pos - neg) / max(1, pos + neg)
    return max(-1.0, min(1.0, float(score)))


def analyze_tone_context(texts: list[str]) -> dict:
    items = [str(t or "").strip() for t in (texts or []) if str(t or "").strip()]
    if not items:
        return {
            "available": True,
            "method": "heuristic",
            "samples": 0,
            "avg_score": 0.0,
            "negative_share_pct": 0.0,
            "bands": {"positive": 0, "neutral": 0, "negative": 0},
        }
    scores = [_heuristic_score(t) for t in items]
    n = len(scores)
    cpos = sum(1 for s in scores if s > 0.15)
    cneg = sum(1 for s in scores if s < -0.15)
    cneu = n - cpos - cneg
    avg = sum(scores) / max(1, n)
    return {
        "available": True,
        "method": "heuristic",
        "samples": n,
        "avg_score": round(float(avg), 4),
        "negative_share_pct": round((cneg / n) * 100.0, 1),
        "bands": {"positive": cpos, "neutral": cneu, "negative": cneg},
    }


class ToneAnalyzer:
    def analyze_single(self, text: str) -> dict:
        s = _heuristic_score(text)
        return {"positive": max(0.0, s), "negative": max(0.0, -s), "neutral": 1.0 - abs(s), "score": s}

    def analyze_batch(self, texts: list[str]) -> list[dict]:
        return [self.analyze_single(t) for t in (texts or [])]
