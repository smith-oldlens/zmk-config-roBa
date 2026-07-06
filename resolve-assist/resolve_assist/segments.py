"""時間区間 (Segment) の集合演算。

無音区間の反転、マージン付与、フィラー区間の除去などパイプラインの
中核となる区間処理をここに集約する。すべて秒単位。
"""

from __future__ import annotations

from .types import Segment


def merge_segments(segments: list[Segment], gap: float = 0.0) -> list[Segment]:
    """重なり・隣接 (間隔 <= gap) の区間を結合してソート済みで返す。"""
    if not segments:
        return []
    ordered = sorted(segments, key=lambda s: s.start)
    merged = [Segment(ordered[0].start, ordered[0].end)]
    for seg in ordered[1:]:
        last = merged[-1]
        if seg.start <= last.end + gap:
            last.end = max(last.end, seg.end)
        else:
            merged.append(Segment(seg.start, seg.end))
    return merged


def invert_segments(
    segments: list[Segment], total_duration: float
) -> list[Segment]:
    """[0, total_duration] の中で segments に含まれない区間を返す。"""
    result: list[Segment] = []
    pos = 0.0
    for seg in merge_segments(segments):
        if seg.start > pos:
            result.append(Segment(pos, min(seg.start, total_duration)))
        pos = max(pos, seg.end)
        if pos >= total_duration:
            break
    if pos < total_duration:
        result.append(Segment(pos, total_duration))
    return [s for s in result if s.duration > 1e-9]


def pad_segments(
    segments: list[Segment],
    pad_before: float,
    pad_after: float,
    total_duration: float,
) -> list[Segment]:
    """各区間の前後にマージンを付け、範囲を [0, total_duration] に収める。"""
    padded = [
        Segment(max(0.0, s.start - pad_before), min(total_duration, s.end + pad_after))
        for s in segments
    ]
    return merge_segments(padded)


def subtract_segments(
    segments: list[Segment], remove: list[Segment], min_remainder: float = 0.05
) -> list[Segment]:
    """segments から remove の区間を取り除く。

    削り取った結果 min_remainder 秒未満になった断片は捨てる
    (フィラー除去で発生する極小クリップを防ぐ)。
    """
    removals = merge_segments(remove)
    result: list[Segment] = []
    for seg in segments:
        pieces = [Segment(seg.start, seg.end)]
        for rem in removals:
            next_pieces: list[Segment] = []
            for p in pieces:
                if rem.end <= p.start or rem.start >= p.end:
                    next_pieces.append(p)
                    continue
                if rem.start > p.start:
                    next_pieces.append(Segment(p.start, rem.start))
                if rem.end < p.end:
                    next_pieces.append(Segment(rem.end, p.end))
            pieces = next_pieces
        result.extend(p for p in pieces if p.duration >= min_remainder)
    return result


def drop_short_segments(segments: list[Segment], min_duration: float) -> list[Segment]:
    """min_duration 秒未満の区間を捨てる。"""
    return [s for s in segments if s.duration >= min_duration]
