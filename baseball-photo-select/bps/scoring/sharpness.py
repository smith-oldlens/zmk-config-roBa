"""Subject sharpness with session calibration (spec 02 §6.3).

Measuring the whole frame would punish exactly the shots worth keeping — a
panned runner has a deliberately smeared background, a long lens at f/2.8 has
almost nothing in focus — so the measurement is confined to the subject crop.

The raw Laplacian variance is not comparable between lenses, light levels or
grounds, so it is never compared against a fixed threshold. Instead each frame
is ranked against the rest of the same session: "soft for this game" is the
only judgement that transfers.
"""
from __future__ import annotations

from typing import Sequence

import cv2
import numpy as np

from .subject import Box

CROP_PADDING = 0.10  # spec §6.3: pad the box by 10% before measuring
ANALYSIS_LONG_EDGE = 512
MIN_CALIBRATION_SAMPLES = 50  # below this, fall back to the bootstrap threshold
#: Raw values are rounded here, at the source, so the number held in memory is
#: bit-identical to the one persisted in scores_json. Percentile ranking counts
#: ties explicitly, and comparing a full-precision value against reloaded,
#: rounded ones breaks that — the same photo would score differently in a fresh
#: run and a resumed one, which can flip it across the keeper threshold.
RAW_DECIMALS = 4


def crop_with_padding(image: np.ndarray, box: Box, padding: float = CROP_PADDING) -> np.ndarray:
    height, width = image.shape[:2]
    pad_x = int(box.w * padding)
    pad_y = int(box.h * padding)
    x0 = max(0, box.x - pad_x)
    y0 = max(0, box.y - pad_y)
    x1 = min(width, box.x + box.w + pad_x)
    y1 = min(height, box.y + box.h + pad_y)
    if x1 <= x0 or y1 <= y0:
        return image
    return image[y0:y1, x0:x1]


def _downscale(image: np.ndarray, long_edge: int = ANALYSIS_LONG_EDGE) -> np.ndarray:
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= long_edge:
        return image
    scale = long_edge / longest
    return cv2.resize(
        image, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA
    )


def raw_sharpness(image: np.ndarray, box: Box | None = None) -> float:
    """log10(1 + Laplacian variance) over the (padded) subject crop."""
    if image is None or image.size == 0:
        return 0.0
    crop = crop_with_padding(image, box) if box is not None else image
    crop = _downscale(crop)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return round(float(np.log10(1.0 + variance)), RAW_DECIMALS)


def percentile_of(value: float, distribution: Sequence[float]) -> float:
    """Fraction of the session that is no sharper than `value`, in 0..1."""
    values = sorted(distribution)
    if not values:
        return 0.5
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    # Midpoint of the tied block keeps identical frames from splitting across a
    # threshold purely by sort order.
    return (below + equal / 2.0) / len(values)


def calibrate(
    value: float,
    distribution: Sequence[float],
    bootstrap_log10: float,
    min_samples: int = MIN_CALIBRATION_SAMPLES,
) -> float:
    """Map a raw sharpness to 0..1.

    With enough of a session to compare against, that is the percentile. Early
    on — the first frames of a game — there is no distribution yet, so the value
    is scored as a ratio against the configured bootstrap threshold instead.
    """
    if len(distribution) >= min_samples:
        return percentile_of(value, distribution)
    if bootstrap_log10 <= 0:
        return 1.0 if value > 0 else 0.0
    return max(0.0, min(1.0, value / bootstrap_log10))
