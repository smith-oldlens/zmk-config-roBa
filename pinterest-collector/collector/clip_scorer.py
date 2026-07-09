"""Optional CLIP-based image scoring.

Scores each pin image against natural-language prompts describing what you
like and dislike, so pins are judged by what the picture *looks like*, not
just its caption. Requires the extra dependencies:

    pip install -r requirements-clip.txt
"""
from __future__ import annotations

import io
import logging

import requests
import torch
import open_clip
from PIL import Image

from .models import Pin

log = logging.getLogger(__name__)

_MODEL_NAME = "ViT-B-32"
_PRETRAINED = "laion2b_s34b_b79k"


def add_clip_scores(pins: list[Pin], clip_cfg: dict) -> None:
    positive = clip_cfg.get("positive_prompts") or []
    negative = clip_cfg.get("negative_prompts") or []
    weight = float(clip_cfg.get("weight", 3.0))
    if not positive and not negative:
        return

    model, _, preprocess = open_clip.create_model_and_transforms(_MODEL_NAME, pretrained=_PRETRAINED)
    tokenizer = open_clip.get_tokenizer(_MODEL_NAME)
    model.eval()

    prompts = positive + negative
    with torch.no_grad():
        text_features = model.encode_text(tokenizer(prompts))
        text_features /= text_features.norm(dim=-1, keepdim=True)

    for pin in pins:
        if not pin.image_url:
            continue
        try:
            resp = requests.get(pin.image_url, timeout=30)
            resp.raise_for_status()
            image = Image.open(io.BytesIO(resp.content)).convert("RGB")
        except Exception as exc:  # noqa: BLE001 - any bad image just skips CLIP
            log.debug("CLIP: could not load image for pin %s: %s", pin.id, exc)
            continue

        with torch.no_grad():
            image_features = model.encode_image(preprocess(image).unsqueeze(0))
            image_features /= image_features.norm(dim=-1, keepdim=True)
            sims = (image_features @ text_features.T).squeeze(0)

        pos_sim = sims[: len(positive)].max().item() if positive else 0.0
        neg_sim = sims[len(positive):].max().item() if negative else 0.0
        clip_score = (pos_sim - neg_sim) * weight

        pin.score += clip_score
        pin.score_details["clip"] = round(clip_score, 3)
