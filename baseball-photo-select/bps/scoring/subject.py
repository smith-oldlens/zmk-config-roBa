"""Main-subject location (spec 02 §6.2).

Youth baseball frames contain the batter, catcher, umpire, fielders and often a
row of parents — picking the largest person box would routinely measure the
wrong body. The camera already knows who the photographer was aiming at, so the
AF point decides, and geometry is only the fallback.

The RTMDet-nano detector is not wired up yet (it needs the ONNX weights, see
docs/03 M2). Until then `detect_persons` is None and every frame takes the
centre-crop fallback, which the spec already defines for zero detections — the
pipeline runs end to end, just less precisely.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

import numpy as np

CENTER_CROP_FRACTION = 0.40  # spec §6.2 step 4
#: The camera's AF frame is tiny — eye tracking reports ~285x417 px on a
#: 7008x4672 frame. Measuring a box that small against other photos' much larger
#: crops would compare different spatial scales, so the AF box is grown to at
#: least this fraction of the short edge. It stays centred on the AF point, so
#: it still covers the tracked player's head and shoulders rather than the
#: middle of the frame.
AF_BOX_MIN_FRACTION = 0.20


@dataclass(frozen=True)
class Box:
    """Pixel box in full-resolution image coordinates."""

    x: int
    y: int
    w: int
    h: int

    @property
    def area(self) -> int:
        return max(0, self.w) * max(0, self.h)

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2.0, self.y + self.h / 2.0

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h

    def as_list(self) -> list[int]:
        return [self.x, self.y, self.w, self.h]


class PersonDetector(Protocol):
    """Anything that returns person boxes for an image (RTMDet-nano in M2+)."""

    def __call__(self, image: np.ndarray) -> Sequence[Box]: ...


def center_box(image: np.ndarray, fraction: float = CENTER_CROP_FRACTION) -> Box:
    """The centre crop used when no person is detected (spec §6.2 step 4)."""
    height, width = image.shape[:2]
    w = max(1, int(width * fraction))
    h = max(1, int(height * fraction))
    return Box((width - w) // 2, (height - h) // 2, w, h)


def box_around(
    image: np.ndarray,
    point: tuple[float, float],
    frame: tuple[int, int] | None = None,
    min_fraction: float = AF_BOX_MIN_FRACTION,
) -> Box:
    """A measurement box centred on the AF point, clamped inside the image.

    `frame` is the camera's own AF frame size when known; it is only ever grown,
    never shrunk, so every photo is measured at a comparable spatial scale.
    """
    height, width = image.shape[:2]
    minimum = max(1, int(min(width, height) * min_fraction))
    w = max(minimum, frame[0] if frame else 0)
    h = max(minimum, frame[1] if frame else 0)
    w, h = min(w, width), min(h, height)
    x = int(round(point[0] - w / 2))
    y = int(round(point[1] - h / 2))
    x = max(0, min(x, width - w))
    y = max(0, min(y, height - h))
    return Box(x, y, w, h)


def select_subject(
    boxes: Sequence[Box],
    af_point: tuple[float, float] | None,
    image_size: tuple[int, int],
    center_sigma: float = 0.35,
) -> tuple[Box | None, str]:
    """Choose the main subject from candidate boxes.

    Returns (box, reason) where reason records how the choice was made so it can
    be stored in scores_json and audited later.
    """
    if not boxes:
        return None, "none"

    width, height = image_size
    if af_point is not None:
        px, py = af_point
        hits = [b for b in boxes if b.contains(px, py)]
        if hits:
            # Smallest box wins: a huge foreground fielder often encloses the AF
            # point as well as the player actually focused on.
            return min(hits, key=lambda b: b.area), "af"

    # Geometry fallback: big and central beats small and peripheral (spec §6.2 step 3).
    diagonal_sq = float(width**2 + height**2) or 1.0
    max_area = float(max(b.area for b in boxes)) or 1.0
    cx, cy = width / 2.0, height / 2.0

    def score(box: Box) -> float:
        bx, by = box.center
        d2 = ((bx - cx) ** 2 + (by - cy) ** 2) / diagonal_sq
        return (box.area / max_area) * math.exp(-d2 / (2 * center_sigma**2))

    return max(boxes, key=score), "center_weighted"


def find_subject(
    image: np.ndarray,
    af_point: tuple[float, float] | None = None,
    detector: PersonDetector | Callable[[np.ndarray], Sequence[Box]] | None = None,
    center_sigma: float = 0.35,
    af_frame: tuple[int, int] | None = None,
) -> tuple[Box, str]:
    """Locate the subject, always returning a measurable box.

    The second element records how the box was chosen:
      'af'             - a detected person containing the AF point
      'center_weighted'- geometry, because the AF point matched no detection
      'af_box'         - built from the AF coordinates alone (no detector)
      'center'         - detector found nobody
      'no_detector'    - no detector and no usable AF data

    With no detector wired up, the camera's own AF position still identifies the
    subject far better than the centre of the frame does — on the α7C II it is
    typically the tracked player's eye (docs/OPEN_QUESTIONS.md, M0).
    """
    height, width = image.shape[:2]
    if detector is None:
        if af_point is not None:
            return box_around(image, af_point, af_frame), "af_box"
        return center_box(image), "no_detector"

    boxes = list(detector(image))
    box, reason = select_subject(boxes, af_point, (width, height), center_sigma)
    if box is None:
        if af_point is not None:
            return box_around(image, af_point, af_frame), "af_box"
        return center_box(image), "center"
    return box, reason
