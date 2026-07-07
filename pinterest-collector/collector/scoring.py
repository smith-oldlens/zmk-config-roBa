"""Preference scoring.

Core scoring is keyword based: each like/dislike keyword found in the pin's
title+description adds its (positive or negative) weight. Optionally, a CLIP
model scores the image itself against natural-language prompts — see
clip_scorer.py.
"""
from __future__ import annotations

import logging

from .models import Pin

log = logging.getLogger(__name__)


def _normalize_keywords(entries: list) -> list[tuple[str, float]]:
    """Accept both `- word` and `- {word: ..., weight: ...}` config styles."""
    result = []
    for entry in entries or []:
        if isinstance(entry, str):
            result.append((entry.lower(), 1.0))
        elif isinstance(entry, dict) and entry.get("word"):
            result.append((str(entry["word"]).lower(), float(entry.get("weight", 1.0))))
    return result


def score_pins(pins: list[Pin], prefs: dict) -> list[Pin]:
    likes = _normalize_keywords(prefs.get("like_keywords"))
    dislikes = _normalize_keywords(prefs.get("dislike_keywords"))

    for pin in pins:
        text = pin.text
        keyword_score = 0.0
        hits = []
        for word, weight in likes:
            if word in text:
                keyword_score += abs(weight)
                hits.append(f"+{word}")
        for word, weight in dislikes:
            if word in text:
                keyword_score -= abs(weight)
                hits.append(f"-{word}")
        pin.score = keyword_score
        pin.score_details = {"keywords": hits}

    clip_cfg = prefs.get("clip") or {}
    if clip_cfg.get("enabled"):
        try:
            from .clip_scorer import add_clip_scores

            add_clip_scores(pins, clip_cfg)
        except ImportError:
            log.warning(
                "CLIP scoring is enabled but torch/open_clip are not installed; "
                "run: pip install -r requirements-clip.txt"
            )

    return pins
