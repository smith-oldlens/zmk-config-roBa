"""Shared fixtures: synthetic JPEGs with real EXIF (spec 02 §12).

Tests must never need a camera. These helpers build JPEGs whose EXIF carries a
controllable DateTimeOriginal / SubSecTimeOriginal so burst grouping, renaming
and the completeness checks can all be exercised deterministically.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import piexif
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bps.config import Config  # noqa: E402  (path set up above)

PIL = pytest.importorskip("PIL", reason="Pillow needed to synthesise test JPEGs")
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402


def write_jpeg(
    path: Path,
    stamp: datetime,
    *,
    size: tuple[int, int] = (320, 240),
    sharp: bool = True,
    fill: tuple[int, int, int] = (90, 110, 140),
    blur: float = 0.0,
) -> Path:
    """Write a JPEG with EXIF DateTimeOriginal/SubSecTimeOriginal set to `stamp`.

    `sharp=False` (or `blur`) produces a frame the sharpness stage should rank
    low; a flat `fill` of black or white produces an exposure blowout.
    """
    img = Image.new("RGB", size, fill)
    if sharp:
        draw = ImageDraw.Draw(img)
        for x in range(0, size[0], 16):
            draw.line((x, 0, x, size[1]), fill=(255, 255, 255), width=2)
        for y in range(0, size[1], 16):
            draw.line((0, y, size[0], y), fill=(20, 20, 20), width=2)
    if blur > 0:
        img = img.filter(ImageFilter.GaussianBlur(radius=blur))
    exif = {
        "0th": {piexif.ImageIFD.Make: b"SONY", piexif.ImageIFD.Model: b"ILCE-7CM2"},
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: stamp.strftime("%Y:%m:%d %H:%M:%S").encode(),
            piexif.ExifIFD.SubSecTimeOriginal: f"{stamp.microsecond // 1000:03d}".encode(),
        },
        "GPS": {},
        "1st": {},
        "thumbnail": None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=88, exif=piexif.dump(exif))
    return path


def write_burst(
    directory: Path,
    count: int,
    *,
    start: datetime | None = None,
    interval_ms: int = 100,
    first_number: int = 1,
    prefix: str = "DSC",
) -> list[Path]:
    """Write `count` JPEGs named DSC0000N.JPG spaced `interval_ms` apart."""
    start = start or datetime(2026, 7, 20, 13, 30, 5, 0)
    paths = []
    for i in range(count):
        stamp = start + timedelta(milliseconds=interval_ms * i)
        name = f"{prefix}{first_number + i:05d}.JPG"
        paths.append(write_jpeg(directory / name, stamp))
    return paths


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """A Config rooted in tmp_path with directories created."""
    config = Config(base_dir=tmp_path / "bps")
    # No stability wait in tests: fixtures are written before the call, at rest.
    config.ingest.size_stable_seconds = 0.0
    config.ensure_dirs()
    return config


@pytest.fixture
def database(cfg: Config):
    from bps.db import Database

    db = Database(cfg.db_path)
    db.init_schema()
    yield db
    db.close()


@pytest.fixture
def card_dir(tmp_path: Path) -> Path:
    """An 'external' directory standing in for a memory card."""
    d = tmp_path / "card" / "100MSDCF"
    d.mkdir(parents=True)
    return d
