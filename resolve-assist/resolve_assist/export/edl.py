"""CMX3600 EDL の生成。

Resolve のスクリプト実行がうまくいかない場合の保険経路。
Resolve のメディアプールでクリップを選択 → File > Import > Timeline で
この EDL を読み込むと、カット済みタイムラインが再現される。

タイムコードはノンドロップフレームで出力する。29.97fps 等の非整数
フレームレートではフレーム番号ベースの NDF タイムコードとして扱う。
"""

from __future__ import annotations

from pathlib import Path

from ..types import Segment


def frames_to_timecode(frames: int, fps: float) -> str:
    base = max(1, int(round(fps)))
    f = frames % base
    total_sec = frames // base
    h, rem = divmod(total_sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def format_edl(
    segments: list[Segment],
    fps: float,
    title: str,
    clip_name: str,
) -> str:
    lines = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    record_frame = 0
    for i, seg in enumerate(segments, start=1):
        src_in = int(round(seg.start * fps))
        src_out = int(round(seg.end * fps))
        length = src_out - src_in
        rec_in = record_frame
        rec_out = record_frame + length
        record_frame = rec_out
        lines.append(
            f"{i:03d}  AX       AA/V  C        "
            f"{frames_to_timecode(src_in, fps)} {frames_to_timecode(src_out, fps)} "
            f"{frames_to_timecode(rec_in, fps)} {frames_to_timecode(rec_out, fps)}"
        )
        lines.append(f"* FROM CLIP NAME: {clip_name}")
        lines.append("")
    return "\n".join(lines)


def write_edl(
    segments: list[Segment],
    fps: float,
    path: str | Path,
    title: str,
    clip_name: str,
) -> Path:
    path = Path(path)
    path.write_text(format_edl(segments, fps, title, clip_name), encoding="utf-8")
    return path
