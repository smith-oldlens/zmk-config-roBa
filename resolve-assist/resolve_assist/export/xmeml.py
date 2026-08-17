"""FCP7 XML (xmeml) の生成 (Premiere Pro 用)。

カット済みシーケンスを xmeml v4 として出力する。Premiere Pro に
File > Import で読み込むと、カット済みシーケンスがプロジェクトに追加される。
フィラー位置などのマーカーはシーケンスマーカーとして再現される。

時間はタイムベース (整数フレームレート) 単位の整数フレーム。
29.97fps 等の NTSC 系は timebase=30 + ntsc=TRUE で表現する。
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from ..types import Marker, MediaInfo, Segment
from .cutlist import sec_to_frame

_NTSC_RATES = {23.976: 24, 29.97: 30, 47.952: 48, 59.94: 60}


def rate_for_fps(fps: float) -> tuple[int, bool]:
    """fps から (timebase, ntsc) を求める。"""
    for ntsc_fps, base in _NTSC_RATES.items():
        if abs(fps - ntsc_fps) < 0.01:
            return base, True
    return max(1, round(fps)), False


def _rate_el(parent: ET.Element, timebase: int, ntsc: bool) -> None:
    rate = ET.SubElement(parent, "rate")
    ET.SubElement(rate, "timebase").text = str(timebase)
    ET.SubElement(rate, "ntsc").text = "TRUE" if ntsc else "FALSE"


def _text_el(parent: ET.Element, tag: str, text: str) -> ET.Element:
    el = ET.SubElement(parent, tag)
    el.text = text
    return el


def _source_frame_to_timeline_frame(
    source_frame: int, frame_segments: list[tuple[int, int]]
) -> int | None:
    offset = 0
    for start, end in frame_segments:
        if start <= source_frame < end:
            return offset + (source_frame - start)
        offset += end - start
    return None


def _clipitem(
    parent: ET.Element,
    *,
    item_id: str,
    name: str,
    timebase: int,
    ntsc: bool,
    total_frames: int,
    src_in: int,
    src_out: int,
    rec_in: int,
    rec_out: int,
    file_el: ET.Element | None,
    file_id: str,
    audio: bool,
    link_ids: list[tuple[str, str]],
) -> None:
    clip = ET.SubElement(parent, "clipitem", id=item_id)
    _text_el(clip, "name", name)
    _text_el(clip, "enabled", "TRUE")
    _text_el(clip, "duration", str(total_frames))
    _rate_el(clip, timebase, ntsc)
    _text_el(clip, "start", str(rec_in))
    _text_el(clip, "end", str(rec_out))
    _text_el(clip, "in", str(src_in))
    _text_el(clip, "out", str(src_out))
    if file_el is not None:
        clip.append(file_el)
    else:
        ET.SubElement(clip, "file", id=file_id)
    if audio:
        st = ET.SubElement(clip, "sourcetrack")
        _text_el(st, "mediatype", "audio")
        _text_el(st, "trackindex", "1")
    for ref_id, mediatype in link_ids:
        link = ET.SubElement(clip, "link")
        _text_el(link, "linkclipref", ref_id)
        _text_el(link, "mediatype", mediatype)
        _text_el(link, "trackindex", "1")


def build_xmeml(
    info: MediaInfo,
    segments: list[Segment],
    markers: list[Marker] | None = None,
    sequence_name: str | None = None,
) -> str:
    source = Path(info.path).resolve()
    timebase, ntsc = rate_for_fps(info.fps)
    total_frames = sec_to_frame(info.duration, info.fps)
    name = sequence_name or f"{source.stem}_cut"

    xmeml = ET.Element("xmeml", version="4")
    sequence = ET.SubElement(xmeml, "sequence")
    _text_el(sequence, "name", name)
    frame_segments = [
        (sec_to_frame(s.start, info.fps), sec_to_frame(s.end, info.fps))
        for s in segments
    ]
    frame_segments = [(a, b) for a, b in frame_segments if b > a]
    kept = sum(b - a for a, b in frame_segments)
    _text_el(sequence, "duration", str(kept))
    _rate_el(sequence, timebase, ntsc)

    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")
    if info.width > 0 and info.height > 0:
        fmt = ET.SubElement(video, "format")
        sc = ET.SubElement(fmt, "samplecharacteristics")
        _text_el(sc, "width", str(info.width))
        _text_el(sc, "height", str(info.height))
    vtrack = ET.SubElement(video, "track")
    audio_el = ET.SubElement(media, "audio")
    atrack = ET.SubElement(audio_el, "track") if info.has_audio else None

    # file 要素は最初の1回だけ実体を書き、以降は id 参照にする (xmeml の慣習)
    file_id = "file-1"
    file_el = ET.Element("file", id=file_id)
    _text_el(file_el, "name", source.name)
    _text_el(file_el, "pathurl", source.as_uri())
    _rate_el(file_el, timebase, ntsc)
    _text_el(file_el, "duration", str(total_frames))
    fmedia = ET.SubElement(file_el, "media")
    if info.width > 0:
        ET.SubElement(fmedia, "video")
    if info.has_audio:
        ET.SubElement(fmedia, "audio")

    rec = 0
    first_file_used = False
    for i, (src_in, src_out) in enumerate(frame_segments, start=1):
        length = src_out - src_in
        v_id, a_id = f"clipitem-v{i}", f"clipitem-a{i}"
        links = [(v_id, "video")] + ([(a_id, "audio")] if atrack is not None else [])
        _clipitem(
            vtrack,
            item_id=v_id,
            name=source.name,
            timebase=timebase,
            ntsc=ntsc,
            total_frames=total_frames,
            src_in=src_in,
            src_out=src_out,
            rec_in=rec,
            rec_out=rec + length,
            file_el=file_el if not first_file_used else None,
            file_id=file_id,
            audio=False,
            link_ids=links,
        )
        first_file_used = True
        if atrack is not None:
            _clipitem(
                atrack,
                item_id=a_id,
                name=source.name,
                timebase=timebase,
                ntsc=ntsc,
                total_frames=total_frames,
                src_in=src_in,
                src_out=src_out,
                rec_in=rec,
                rec_out=rec + length,
                file_el=None,
                file_id=file_id,
                audio=True,
                link_ids=links,
            )
        rec += length

    # マーカー: ソース位置 → カット後タイムライン位置へ変換してシーケンスに打つ
    for marker in markers or []:
        mf = sec_to_frame(marker.sec, info.fps)
        tl = _source_frame_to_timeline_frame(mf, frame_segments)
        if tl is None:
            continue
        m = ET.SubElement(sequence, "marker")
        _text_el(m, "name", marker.name)
        _text_el(m, "comment", marker.note)
        _text_el(m, "in", str(tl))
        _text_el(m, "out", "-1")

    body = ET.tostring(xmeml, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n{body}\n'


def write_xmeml(
    info: MediaInfo,
    segments: list[Segment],
    path: str | Path,
    markers: list[Marker] | None = None,
    sequence_name: str | None = None,
) -> Path:
    path = Path(path)
    path.write_text(
        build_xmeml(info, segments, markers, sequence_name), encoding="utf-8"
    )
    return path
