"""SRT 字幕の生成とパース。日本語向けの行折り返し・分割整形付き。"""

from __future__ import annotations

import re
from pathlib import Path

from ..jp_text import wrap_japanese
from ..types import TranscriptSegment

_SRT_TIME_RE = re.compile(
    r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)"
)

# wrap_japanese は jp_text へ移動 (BudouX 対応)。後方互換のため re-export する。
__all__ = [
    "wrap_japanese",
    "format_srt",
    "parse_srt",
    "write_srt",
    "split_segment_for_subtitles",
]


def _format_timestamp(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    ms = round(sec * 1000)
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def split_segment_for_subtitles(
    seg: TranscriptSegment, max_chars: int, max_lines: int
) -> list[TranscriptSegment]:
    """1画面に収まらない長いセグメントを複数の字幕エントリに分割する。

    単語タイムスタンプがあればそれで時間を割り、なければ文字数比で按分する。
    """
    lines = wrap_japanese(seg.text, max_chars)
    if len(lines) <= max_lines:
        return [seg]

    # max_lines 行ずつのチャンクに分ける
    chunks = [
        "\n".join(lines[i : i + max_lines]) for i in range(0, len(lines), max_lines)
    ]
    total_chars = sum(len(c.replace("\n", "")) for c in chunks) or 1
    duration = seg.end - seg.start
    result: list[TranscriptSegment] = []
    t = seg.start
    for chunk in chunks:
        share = len(chunk.replace("\n", "")) / total_chars
        end = min(seg.end, t + duration * share)
        result.append(TranscriptSegment(start=t, end=end, text=chunk))
        t = end
    if result:
        result[-1].end = seg.end
    return result


def parse_srt(text: str) -> list[TranscriptSegment]:
    """SRT 文字列をパースする。text には行構造 (\\n) を保持する。"""
    segments: list[TranscriptSegment] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip())
    for block in blocks:
        lines = block.split("\n")
        time_idx = next(
            (i for i, l in enumerate(lines) if _SRT_TIME_RE.search(l)), None
        )
        if time_idx is None:
            continue
        m = _SRT_TIME_RE.search(lines[time_idx])
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(g) for g in m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        body = "\n".join(lines[time_idx + 1 :]).strip()
        if body:
            segments.append(TranscriptSegment(start=start, end=end, text=body))
    return segments


def format_srt(
    segments: list[TranscriptSegment],
    max_chars_per_line: int = 26,
    max_lines: int = 2,
    min_gap: float = 0.001,
    min_duration: float = 0.0,
) -> str:
    """TranscriptSegment のリストから SRT 文字列を生成する。"""
    entries: list[TranscriptSegment] = []
    for seg in segments:
        if not seg.text.strip():
            continue
        entries.extend(split_segment_for_subtitles(seg, max_chars_per_line, max_lines))

    # 前後の字幕が重ならないように調整
    for prev, cur in zip(entries, entries[1:]):
        if cur.start < prev.end:
            cur.start = prev.end + min_gap

    # 最短表示時間を確保 (次の字幕に食い込まない範囲で延長)
    if min_duration > 0:
        for i, seg in enumerate(entries):
            limit = entries[i + 1].start - min_gap if i + 1 < len(entries) else float("inf")
            new_end = min(seg.start + min_duration, limit)
            if new_end > seg.end:
                seg.end = new_end

    out: list[str] = []
    for i, seg in enumerate(entries, start=1):
        text = seg.text if "\n" in seg.text else "\n".join(
            wrap_japanese(seg.text, max_chars_per_line)
        )
        out.append(
            f"{i}\n{_format_timestamp(seg.start)} --> {_format_timestamp(seg.end)}\n{text}\n"
        )
    return "\n".join(out)


def write_srt(segments: list[TranscriptSegment], path: str | Path, **kwargs) -> Path:
    path = Path(path)
    path.write_text(format_srt(segments, **kwargs), encoding="utf-8")
    return path
