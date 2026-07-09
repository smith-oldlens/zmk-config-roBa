"""Perceptual duplicate detection via dHash (difference hash).

The same artwork is often re-pinned under many different URLs and pin ids,
so URL/id dedupe alone still downloads the same picture repeatedly. dHash
reduces each image to a 64-bit fingerprint of its brightness gradients;
two images whose fingerprints differ in at most `max_distance` bits are
treated as the same picture. Pillow only — no extra dependencies.
"""
from __future__ import annotations

import io
import logging

from PIL import Image

log = logging.getLogger(__name__)

_HASH_W, _HASH_H = 9, 8  # 9x8 grayscale -> 8x8 horizontal differences = 64 bits


def dhash(image_bytes: bytes) -> str | None:
    """64-bit dHash as a 16-char hex string, or None for undecodable data."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L").resize(
            (_HASH_W, _HASH_H), Image.LANCZOS
        )
    except Exception as exc:  # noqa: BLE001 - any broken image just skips hashing
        log.debug("dhash: could not decode image: %s", exc)
        return None
    px = list(img.getdata())
    bits = 0
    for row in range(_HASH_H):
        for col in range(_HASH_W - 1):
            i = row * _HASH_W + col
            bits = (bits << 1) | (1 if px[i] > px[i + 1] else 0)
    return f"{bits:016x}"


def hamming(hex_a: str, hex_b: str) -> int:
    return bin(int(hex_a, 16) ^ int(hex_b, 16)).count("1")


def find_duplicate(new_hash: str, known: dict[str, str], max_distance: int) -> str | None:
    """Return the pin id of a known image within max_distance bits, else None."""
    for pin_id, known_hash in known.items():
        try:
            if hamming(new_hash, known_hash) <= max_distance:
                return pin_id
        except ValueError:
            continue
    return None
