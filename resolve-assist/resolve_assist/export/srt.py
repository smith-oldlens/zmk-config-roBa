"""SRT 字幕の生成。日本語向けの行折り返し・分割整形付き。"""

from __future__ import annotations

from pathlib import Path

from ..types import TranscriptSegment

# 行を折り返しやすい文字(この直後で改行する)
_BREAK_AFTER = "、。!?!?…とがはをにでへもね"


def _format_timestamp(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    ms = round(sec * 1000)
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def wrap_japanese(text: str, max_chars: int) -> list[str]:
    """日本語テキストを最大 max_chars 文字で行に折り返す。

    可能なら句読点・助詞の直後で折る。
    """
    text = text.strip()
    lines: list[str] = []
    while len(text) > max_chars:
        window = text[: max_chars + 1]
        break_at = -1
        # 後ろから折り返し候補を探す(先頭付近で折ると不格好なので 1/3 以降)
        for i in range(len(window) - 1, max(1, max_chars // 3), -1):
            if window[i - 1] in _BREAK_AFTER:
                break_at = i
                break
        if break_at <= 0:
            break_at = max_chars
        lines.append(text[:break_at].strip())
        text = text[break_at:].strip()
    if text:
        lines.append(text)
    return lines


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


def format_srt(
    segments: list[TranscriptSegment],
    max_chars_per_line: int = 26,
    max_lines: int = 2,
    min_gap: float = 0.001,
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
