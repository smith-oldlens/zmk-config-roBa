"""Metadata I/O via a resident exiftool (spec 02 §7).

AF data is the highest-value signal the pipeline has: on the α7C II the camera
reports where it was tracking — in practice on the player's eye — which no
generic person detector can tell us. Confirmed against real match photos in M0
(docs/OPEN_QUESTIONS.md):

    Focus Location   : 7008 4672 4259 2044     (W H X Y, top-left origin)
    Focus Frame Size : 285x417
    AF Area Mode     : Human Eye Tracking

Reading must happen before anything writes XMP to the file: rewriting a JPEG
shifts MakerNotes offsets and the AF data becomes unreadable (docs/01
invariant 1).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .config import Config
from .log import get_logger

log = get_logger("bps.metadata")

#: Sony writes the image centre when it has no AF point, so a coordinate this
#: close to the middle is treated as "no data" (spec §7.2). Real shots measured
#: in M0 sat 10-15% off centre, far outside this band.
CENTER_TOLERANCE = 0.01
_FRAME_SIZE_RE = re.compile(r"(\d+)\s*x\s*(\d+)")


@dataclass(frozen=True)
class AfRegion:
    """Where the camera was focusing, in its own reference frame."""

    ref_width: int
    ref_height: int
    x: int
    y: int
    frame_w: int | None = None
    frame_h: int | None = None
    center_suspect: bool = False
    area_mode: str = ""
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_width": self.ref_width,
            "ref_height": self.ref_height,
            "x": self.x,
            "y": self.y,
            "frame_w": self.frame_w,
            "frame_h": self.frame_h,
            "center_suspect": self.center_suspect,
            "area_mode": self.area_mode,
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AfRegion | None":
        try:
            return cls(
                ref_width=int(data["ref_width"]),
                ref_height=int(data["ref_height"]),
                x=int(data["x"]),
                y=int(data["y"]),
                frame_w=data.get("frame_w"),
                frame_h=data.get("frame_h"),
                center_suspect=bool(data.get("center_suspect", False)),
                area_mode=str(data.get("area_mode", "")),
                raw=str(data.get("raw", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def scaled_to(self, width: int, height: int) -> "AfRegion | None":
        """Map the coordinates onto an image of a different size.

        Returns None when the aspect ratios disagree, which means the image was
        cropped after leaving the camera — the old coordinates would then point
        at a different part of the scene, i.e. potentially a different player.
        """
        if self.ref_width <= 0 or self.ref_height <= 0 or width <= 0 or height <= 0:
            return None
        if width == self.ref_width and height == self.ref_height:
            return self
        ref_aspect = self.ref_width / self.ref_height
        aspect = width / height
        if abs(ref_aspect - aspect) > 0.01 * ref_aspect:
            log.debug(
                "AF frame %dx%d does not match image %dx%d (cropped?); ignoring AF",
                self.ref_width,
                self.ref_height,
                width,
                height,
            )
            return None
        scale_x = width / self.ref_width
        scale_y = height / self.ref_height
        return AfRegion(
            ref_width=width,
            ref_height=height,
            x=int(round(self.x * scale_x)),
            y=int(round(self.y * scale_y)),
            frame_w=int(round(self.frame_w * scale_x)) if self.frame_w else None,
            frame_h=int(round(self.frame_h * scale_y)) if self.frame_h else None,
            center_suspect=self.center_suspect,
            area_mode=self.area_mode,
            raw=self.raw,
        )

    @property
    def point(self) -> tuple[float, float]:
        return float(self.x), float(self.y)


# --- pure parsing --------------------------------------------------------


def parse_focus_location(value: Any) -> tuple[int, int, int, int] | None:
    """'W H X Y' (string, or a list from exiftool) -> (w, h, x, y)."""
    if isinstance(value, (list, tuple)):
        numbers = []
        for item in value:
            try:
                numbers.append(int(item))
            except (TypeError, ValueError):
                return None
    else:
        numbers = [int(n) for n in re.findall(r"-?\d+", str(value))]
    if len(numbers) < 4:
        return None
    return numbers[0], numbers[1], numbers[2], numbers[3]


def parse_frame_size(value: Any) -> tuple[int, int] | None:
    """'285x417' -> (285, 417)."""
    if value is None:
        return None
    match = _FRAME_SIZE_RE.search(str(value))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def is_center_fallback(
    ref_w: int, ref_h: int, x: int, y: int, tolerance: float = CENTER_TOLERANCE
) -> bool:
    """True when the AF point is the image centre, i.e. Sony's "no data" value."""
    if ref_w <= 0 or ref_h <= 0:
        return False
    return abs(x - ref_w / 2) <= ref_w * tolerance and abs(y - ref_h / 2) <= ref_h * tolerance


def build_af_region(tags: dict[str, Any], tag_names: Sequence[str]) -> AfRegion | None:
    """Assemble an AfRegion from an exiftool tag dump, honouring tag priority."""
    for name in tag_names:
        value = _lookup(tags, name)
        if value is None:
            continue
        parsed = parse_focus_location(value)
        if parsed is None:
            continue
        ref_w, ref_h, x, y = parsed
        if ref_w <= 0 or ref_h <= 0:
            continue
        frame = parse_frame_size(_lookup(tags, "MakerNotes:FocusFrameSize"))
        return AfRegion(
            ref_width=ref_w,
            ref_height=ref_h,
            x=x,
            y=y,
            frame_w=frame[0] if frame else None,
            frame_h=frame[1] if frame else None,
            center_suspect=is_center_fallback(ref_w, ref_h, x, y),
            area_mode=str(_lookup(tags, "MakerNotes:AFAreaMode") or ""),
            raw=str(value),
        )
    return None


def _lookup(tags: dict[str, Any], name: str) -> Any:
    """Find a tag whether or not exiftool prefixed it with its group."""
    if name in tags:
        return tags[name]
    bare = name.split(":")[-1]
    for key, value in tags.items():
        if key.split(":")[-1] == bare:
            return value
    return None


# --- exiftool ------------------------------------------------------------


class MetadataTool:
    """Resident exiftool process (spec §7.1).

    Starting exiftool per file costs 200-600ms on Windows, which would dominate
    ingest, so one process is kept alive for the whole run.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._helper = None
        self._unavailable = False

    def __enter__(self) -> "MetadataTool":
        self._start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _start(self) -> bool:
        if self._helper is not None:
            return True
        if self._unavailable:
            return False
        try:
            import exiftool  # imported lazily so M1 works without it
        except ImportError:
            log.warning("pyexiftool not installed — AF data will be skipped")
            self._unavailable = True
            return False
        try:
            self._helper = exiftool.ExifToolHelper(
                executable=self.cfg.exiftool_path, common_args=["-n"]
            )
            self._helper.run()
        except Exception as exc:  # exiftool missing or not executable
            log.warning("could not start exiftool (%s) — AF data will be skipped", exc)
            self._helper = None
            self._unavailable = True
            return False
        return True

    def close(self) -> None:
        if self._helper is not None:
            try:
                self._helper.terminate()
            except Exception:
                pass
            self._helper = None

    @property
    def available(self) -> bool:
        return self._start()

    def read_tags(self, path: Path, tags: Sequence[str]) -> dict[str, Any]:
        if not self._start():
            return {}
        try:
            result = self._helper.get_tags([str(path)], list(tags))
        except Exception as exc:
            log.debug("exiftool read failed for %s: %s", path.name, exc)
            return {}
        return result[0] if result else {}

    def read_af_region(self, path: Path) -> AfRegion | None:
        """AF position for one file. Must be called before any XMP write."""
        wanted = list(self.cfg.af.tag_names) + [
            "MakerNotes:FocusFrameSize",
            "MakerNotes:AFAreaMode",
        ]
        tags = self.read_tags(path, [t.split(":")[-1] for t in wanted])
        if not tags:
            return None
        return build_af_region(tags, self.cfg.af.tag_names)
