"""ショート動画 (YouTube Shorts / Instagram Reels) の生成。

長い動画からハイライト区間を選び (自動提案 or 手動指定)、無音カットで
詰めた縦型 9:16 の mp4 を書き出す。字幕の焼き込み (ASS) にも対応。
そのまま SNS にアップできる形式 (H.264 / AAC / yuv420p / faststart)。
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import media
from .analysis.highlights import Highlight, find_highlights
from .analysis.silence import SilenceOptions, detect_speech_segments
from .export import srt as srt_mod
from .export.ass import write_ass
from .types import Segment, TranscriptSegment

ProgressCb = Callable[[str], None]


@dataclass
class ShortsOptions:
    """ショート生成の全オプション。"""

    count: int = 3                    # 自動提案するハイライト数
    max_duration: float = 60.0        # 1本の上限尺 (Shorts=60, Reels=90)
    min_duration: float = 10.0        # これ未満の候補は捨てる
    fit: str = "blur"                 # 縦型変換: "blur"=ぼかし背景 / "crop"=中央クロップ
    burn_subtitles: bool = True       # 字幕を焼き込む
    ranges: list[tuple[float, float]] | None = None  # 手動指定 (指定時は自動提案しない)

    width: int = 1080
    height: int = 1920
    font: str = "Hiragino Sans"       # 焼き込み字幕のフォント (macOS 標準)
    subtitle_max_chars: int = 13      # 縦型は1行が短い

    silence: SilenceOptions = field(default_factory=SilenceOptions)
    whisper_model: str = "small"
    language: str = "ja"
    output_dir: str | None = None


@dataclass
class ShortClip:
    index: int
    path: Path
    duration: float
    source_start: float
    source_end: float
    preview: str = ""
    score: float = 0.0
    srt_path: Path | None = None


@dataclass
class ShortsResult:
    output_dir: Path
    clips: list[ShortClip] = field(default_factory=list)
    summary_path: Path | None = None


def clip_segments_to_window(
    speech: list[Segment], start: float, end: float
) -> list[Segment]:
    """発話区間を [start, end] 窓で切り取る。"""
    result = []
    for seg in speech:
        a, b = max(seg.start, start), min(seg.end, end)
        if b > a:
            result.append(Segment(a, b))
    return result


def map_transcript_to_clip(
    transcript: list[TranscriptSegment], sub_segments: list[Segment]
) -> list[TranscriptSegment]:
    """ソース時刻の字幕を、カット後クリップ内の時刻に変換する。"""
    events: list[TranscriptSegment] = []
    offset = 0.0
    for sub in sub_segments:
        for seg in transcript:
            a, b = max(seg.start, sub.start), min(seg.end, sub.end)
            if b - a > 0.15:  # 一瞬だけかかる字幕はノイズなので除外
                events.append(
                    TranscriptSegment(
                        start=offset + (a - sub.start),
                        end=offset + (b - sub.start),
                        text=seg.text,
                    )
                )
        offset += sub.duration
    return events


def build_shorts_filtergraph(
    sub_segments: list[Segment],
    fit: str,
    width: int,
    height: int,
    ass_name: str | None = None,
) -> str:
    """トリム+結合+縦型変換+(任意)字幕焼き込みの filter_complex を組み立てる。"""
    parts: list[str] = []
    for i, seg in enumerate(sub_segments):
        parts.append(
            f"[0:v]trim=start={seg.start:.3f}:end={seg.end:.3f},"
            f"setpts=PTS-STARTPTS[v{i}]"
        )
        parts.append(
            f"[0:a]atrim=start={seg.start:.3f}:end={seg.end:.3f},"
            f"asetpts=PTS-STARTPTS[a{i}]"
        )
    n = len(sub_segments)
    inputs = "".join(f"[v{i}][a{i}]" for i in range(n))
    parts.append(f"{inputs}concat=n={n}:v=1:a=1[vc][ac]")

    if fit == "crop":
        parts.append(f"[vc]scale=-2:{height},crop={width}:{height}[vo]")
    else:  # blur: ぼかした引き伸ばし背景の中央に元映像を重ねる
        parts.append(
            f"[vc]split=2[bg][fg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma=25[bgb];"
            f"[fg]scale={width}:-2[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2[vo]"
        )
    if ass_name:
        parts.append(f"[vo]ass={ass_name}[vout]")
    else:
        parts.append("[vo]null[vout]")
    return ";".join(parts)


def _render_clip(
    video_path: Path,
    out_path: Path,
    sub_segments: list[Segment],
    options: ShortsOptions,
    ass_name: str | None,
) -> None:
    ffmpeg = media._require("ffmpeg")
    graph = build_shorts_filtergraph(
        sub_segments, options.fit, options.width, options.height, ass_name
    )
    cmd = [
        ffmpeg, "-y",
        "-i", str(video_path),
        "-filter_complex", graph,
        "-map", "[vout]", "-map", "[ac]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        out_path.name,
    ]
    # ass フィルタのパスエスケープ問題を避けるため出力フォルダを cwd にする
    proc = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(out_path.parent)
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg の書き出しに失敗しました:\n{proc.stderr[-1500:]}"
        )


def make_shorts(
    video_path: str | Path,
    options: ShortsOptions | None = None,
    log: ProgressCb | None = None,
) -> ShortsResult:
    """ショート動画を生成する。"""
    opts = options or ShortsOptions()
    emit = log or (lambda msg: None)
    video_path = Path(video_path).resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"ファイルが見つかりません: {video_path}")

    info = media.probe(video_path)
    if not info.has_audio:
        raise RuntimeError("音声トラックがないためショート生成できません。")
    emit(f"素材: {video_path.name} ({info.duration:.1f} 秒)")

    out_dir = Path(opts.output_dir) if opts.output_dir else (
        video_path.parent / f"{video_path.stem}_shorts"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    emit("無音検出中 (テンポ詰め用)...")
    speech = detect_speech_segments(video_path, info.duration, opts.silence)

    # 文字起こし: 自動ハイライト提案と字幕焼き込みに必要
    transcript: list[TranscriptSegment] = []
    need_transcript = opts.burn_subtitles or opts.ranges is None
    if need_transcript:
        from .analysis import transcribe as tr

        emit(f"文字起こし中 (faster-whisper / {opts.whisper_model})...")
        with tempfile.TemporaryDirectory() as tmp:
            wav = media.extract_audio(video_path, Path(tmp) / "audio.wav")
            transcript = tr.transcribe(
                wav,
                tr.TranscribeOptions(
                    model_size=opts.whisper_model, language=opts.language
                ),
            )
        emit(f"  {len(transcript)} セグメント")

    # 区間の決定: 手動指定 or 自動ハイライト
    if opts.ranges:
        windows = [
            Highlight(start=a, end=b, score=0.0, kept_duration=0.0)
            for a, b in opts.ranges
        ]
        emit(f"手動指定の {len(windows)} 区間をショート化します")
    else:
        windows = find_highlights(
            transcript,
            speech,
            max_duration=opts.max_duration,
            count=opts.count,
            min_duration=opts.min_duration,
        )
        if not windows:
            raise RuntimeError(
                "ハイライト候補が見つかりませんでした。"
                "--range で区間を手動指定するか、--min-duration を下げてください。"
            )
        emit(f"ハイライト候補を {len(windows)} 個選びました")

    result = ShortsResult(output_dir=out_dir)
    summary_lines = [f"== ショート生成サマリー: {video_path.name} ==", ""]
    for k, win in enumerate(windows, start=1):
        sub_segments = clip_segments_to_window(speech, win.start, win.end)
        if not sub_segments:
            sub_segments = [Segment(win.start, win.end)]
        duration = sum(s.duration for s in sub_segments)

        # 字幕 (焼き込み用 ASS + 後編集用 SRT)
        ass_name = None
        srt_path = None
        if transcript:
            events = map_transcript_to_clip(transcript, sub_segments)
            if events:
                srt_path = out_dir / f"short_{k:02d}.srt"
                srt_path.write_text(
                    srt_mod.format_srt(
                        events, max_chars_per_line=opts.subtitle_max_chars
                    ),
                    encoding="utf-8",
                )
                if opts.burn_subtitles:
                    ass_name = f"short_{k:02d}.ass"
                    write_ass(
                        events,
                        out_dir / ass_name,
                        width=opts.width,
                        height=opts.height,
                        font=opts.font,
                        max_chars=opts.subtitle_max_chars,
                    )

        out_path = out_dir / f"short_{k:02d}.mp4"
        emit(
            f"[{k}/{len(windows)}] 書き出し中: {out_path.name} "
            f"({win.start:.1f}s-{win.end:.1f}s → {duration:.1f}s"
            f"{', 字幕焼き込み' if ass_name else ''})"
        )
        _render_clip(video_path, out_path, sub_segments, opts, ass_name)

        result.clips.append(
            ShortClip(
                index=k,
                path=out_path,
                duration=duration,
                source_start=win.start,
                source_end=win.end,
                preview=win.preview,
                score=win.score,
                srt_path=srt_path,
            )
        )
        summary_lines.append(
            f"short_{k:02d}.mp4  元 {win.start:6.1f}s-{win.end:6.1f}s  "
            f"尺 {duration:5.1f}s  スコア {win.score:.1f}\n"
            f"  {win.preview}"
        )

    result.summary_path = out_dir / "shorts_summary.txt"
    result.summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    emit(f"完了! {len(result.clips)} 本を書き出しました → {out_dir}")
    emit("そのまま YouTube Shorts / Instagram Reels にアップできます。")
    return result
