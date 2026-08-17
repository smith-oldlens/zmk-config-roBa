"""ASS 字幕の生成 (ショート動画への焼き込み用)。

ショート動画定番の「大きめ白文字+黒縁取り・下部配置」のスタイルで
ASS を生成し、ffmpeg の ass フィルタで映像に焼き込む。
改行は BudouX (jp_text.wrap_japanese) による文節単位。
"""

from __future__ import annotations

from pathlib import Path

from ..jp_text import wrap_japanese
from ..types import TranscriptSegment


def _ass_timestamp(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    cs = round(sec * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    return text.replace("{", "(").replace("}", ")")


def format_ass(
    events: list[TranscriptSegment],
    width: int = 1080,
    height: int = 1920,
    font: str = "Hiragino Sans",
    font_size: int = 64,
    margin_v: int = 200,
    max_chars: int = 13,
) -> str:
    """字幕イベント列から ASS 文字列を生成する。時刻はクリップ基準の秒。"""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Short,{font},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H7F000000,1,0,0,0,100,100,0,0,1,4,1,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for ev in events:
        text = ev.text.strip()
        if not text or ev.end <= ev.start:
            continue
        wrapped = "\\N".join(_escape(line) for line in wrap_japanese(text, max_chars))
        lines.append(
            f"Dialogue: 0,{_ass_timestamp(ev.start)},{_ass_timestamp(ev.end)},"
            f"Short,,0,0,0,,{wrapped}"
        )
    return "\n".join(lines) + "\n"


def write_ass(events: list[TranscriptSegment], path: str | Path, **kwargs) -> Path:
    path = Path(path)
    path.write_text(format_ass(events, **kwargs), encoding="utf-8")
    return path
