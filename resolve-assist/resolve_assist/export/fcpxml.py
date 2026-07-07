"""FCPXML の生成 (Final Cut Pro 用)。

カット済みタイムライン(発話区間のみを並べたプロジェクト)を FCPXML 1.9
として出力する。Final Cut Pro に File > Import > XML で読み込むと、
イベント「Resolve Assist」内にカット済みプロジェクトが作られる。
フィラー位置などのマーカーはクリップ上のマーカーとして再現される。

FCPXML の時間表現は「フレーム長の整数倍の有理数秒」(例: 1001/30000s)。
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

from ..types import Marker, MediaInfo, Segment
from .cutlist import sec_to_frame

# NTSC 系フレームレートのフレーム長 (分子, 分母)
_NTSC_FRAME_DURATIONS = ((1001, 24000), (1001, 30000), (1001, 48000), (1001, 60000))


def frame_duration_fraction(fps: float) -> tuple[int, int]:
    """fps から FCPXML の frameDuration (分子, 分母) を求める。"""
    for num, den in _NTSC_FRAME_DURATIONS:
        if abs(fps - den / num) < 0.01:
            return num, den
    rounded = round(fps)
    if abs(fps - rounded) < 1e-3 and rounded > 0:
        return 1, rounded
    frac = Fraction(fps).limit_denominator(100000)
    # フレーム長は 1/fps
    return frac.denominator, frac.numerator


def _t(frames: int, num: int, den: int) -> str:
    """フレーム数を FCPXML の有理数秒表現にする。"""
    return f"{frames * num}/{den}s"


def build_fcpxml(
    info: MediaInfo,
    segments: list[Segment],
    markers: list[Marker] | None = None,
    project_name: str | None = None,
) -> str:
    source = Path(info.path).resolve()
    num, den = frame_duration_fraction(info.fps)
    total_frames = sec_to_frame(info.duration, info.fps)
    project = project_name or f"{source.stem}_cut"

    fcpxml = ET.Element("fcpxml", version="1.9")
    resources = ET.SubElement(fcpxml, "resources")
    fmt_attrs = {"id": "r1", "frameDuration": f"{num}/{den}s"}
    if info.width > 0 and info.height > 0:
        fmt_attrs["width"] = str(info.width)
        fmt_attrs["height"] = str(info.height)
    ET.SubElement(resources, "format", **fmt_attrs)
    asset = ET.SubElement(
        resources,
        "asset",
        id="r2",
        name=source.name,
        start="0s",
        duration=_t(total_frames, num, den),
        hasVideo="1" if info.width > 0 else "0",
        hasAudio="1" if info.has_audio else "0",
        format="r1",
    )
    ET.SubElement(asset, "media-rep", kind="original-media", src=source.as_uri())

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", name="Resolve Assist")
    project_el = ET.SubElement(event, "project", name=project)

    kept_frames = sum(
        sec_to_frame(s.end, info.fps) - sec_to_frame(s.start, info.fps)
        for s in segments
    )
    sequence = ET.SubElement(
        project_el,
        "sequence",
        format="r1",
        duration=_t(kept_frames, num, den),
        tcStart="0s",
        tcFormat="NDF",
    )
    spine = ET.SubElement(sequence, "spine")

    offset = 0
    for seg in segments:
        src_in = sec_to_frame(seg.start, info.fps)
        src_out = sec_to_frame(seg.end, info.fps)
        length = src_out - src_in
        if length <= 0:
            continue
        clip = ET.SubElement(
            spine,
            "asset-clip",
            ref="r2",
            name=source.name,
            offset=_t(offset, num, den),
            start=_t(src_in, num, den),
            duration=_t(length, num, den),
            format="r1",
        )
        # このクリップの範囲に入るマーカーをソース時間で打つ
        for marker in markers or []:
            mf = sec_to_frame(marker.sec, info.fps)
            if src_in <= mf < src_out:
                ET.SubElement(
                    clip,
                    "marker",
                    start=_t(mf, num, den),
                    duration=_t(1, num, den),
                    value=marker.name,
                )
        offset += length

    body = ET.tostring(fcpxml, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n{body}\n'


def write_fcpxml(
    info: MediaInfo,
    segments: list[Segment],
    path: str | Path,
    markers: list[Marker] | None = None,
    project_name: str | None = None,
) -> Path:
    path = Path(path)
    path.write_text(
        build_fcpxml(info, segments, markers, project_name), encoding="utf-8"
    )
    return path
