"""Exposure blowout detection (spec 02 §6.1).

This is the only check allowed to reject a frame outright, with no regard for
what else is in the burst: an all-black or all-white frame carries no image at
all. Everything else — even badly blurred — goes through the group comparison,
because a soft frame can still be the only photo of a given child (docs/01 §2).
"""
from __future__ import annotations

import cv2
import numpy as np

BLACK_LEVEL = 5
WHITE_LEVEL = 250
BLOWOUT_FRACTION = 0.98


def exposure_ok(image: np.ndarray) -> bool:
    """False when ~all pixels are crushed black or blown white."""
    if image is None or image.size == 0:
        return False
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    total = gray.size
    if total == 0:
        return False
    histogram = cv2.calcHist([gray], [0], None, [256], [0, 256]).flatten()
    black = float(histogram[:BLACK_LEVEL].sum()) / total
    white = float(histogram[WHITE_LEVEL + 1 :].sum()) / total
    return black < BLOWOUT_FRACTION and white < BLOWOUT_FRACTION
