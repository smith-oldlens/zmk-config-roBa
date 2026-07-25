"""Decisive-moment scoring (spec 02 §6.4) — degraded until Phase 3 (M5).

The real scorer is a SigLIP2 embedding fed to a logistic regression trained on
the photographer's own past Lightroom selects. Until that model exists this
returns 0.0 for every frame, which the star logic handles: keep_score simply
collapses to sharpness alone and no frame is promoted to five stars.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..log import get_logger

log = get_logger("bps.scoring.moment")

_WARNED = False


def classifier_available(classifier_path: str | Path) -> bool:
    return Path(classifier_path).is_file()


def moment_score(image: np.ndarray, classifier_path: str | Path | None = None) -> float:
    """Probability that this frame is a decisive moment, in 0..1.

    Returns 0.0 when the trained classifier is absent so the pipeline stays
    usable before Phase 3 (spec §6.4).
    """
    global _WARNED
    if classifier_path is None or not classifier_available(classifier_path):
        if not _WARNED:
            log.info("no moment classifier yet — scoring on sharpness alone (Phase 3 adds this)")
            _WARNED = True
        return 0.0
    # M5 wires SigLIP2 ONNX embeddings + the trained classifier in here.
    raise NotImplementedError("moment classifier inference lands in M5 (docs/03)")
