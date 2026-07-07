"""解析パイプライン本体。CLI と GUI の両方からこれを呼ぶ。"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import media
from .analysis import silence
from .analysis.fillers import (
    FillerHit,
    detect_fillers,
    filler_cut_segments,
    load_filler_dict,
)
from .export import cutlist as cutlist_mod
from .export import edl as edl_mod
from .export import fcpxml as fcpxml_mod
from .export import srt as srt_mod
from .export import xmeml as xmeml_mod
from .segments import subtract_segments
from .types import Marker, MediaInfo, Segment, TranscriptSegment


@dataclass
class AnalyzeOptions:
    """解析パイプラインの全オプション。"""

    # 実行する処理
    do_silence: bool = True
    do_subtitles: bool = False
    do_fillers: bool = False
    do_scenes: bool = False
    cut_fillers: bool = False       # フィラーを自動カットに含める(既定はマーカーのみ)

    # 各処理のパラメータ
    silence: silence.SilenceOptions = field(default_factory=silence.SilenceOptions)
    whisper_model: str = "small"
    language: str = "ja"
    scene_threshold: float = 27.0
    filler_dict_path: str | None = None
    max_chars_per_line: int = 26

    # 対象の編集ソフト: "resolve" / "fcp" (Final Cut Pro) / "premiere" (Premiere Pro)
    targets: set[str] = field(default_factory=lambda: {"resolve"})

    # 出力先。None なら <動画のあるフォルダ>/<動画名>_assist/
    output_dir: str | None = None


@dataclass
class AnalyzeResult:
    info: MediaInfo
    output_dir: Path
    segments: list[Segment] = field(default_factory=list)
    transcript: list[TranscriptSegment] = field(default_factory=list)
    fillers: list[FillerHit] = field(default_factory=list)
    scene_cuts: list[float] = field(default_factory=list)
    cuts_json: Path | None = None
    edl_path: Path | None = None
    fcpxml_path: Path | None = None
    premiere_xml_path: Path | None = None
    srt_path: Path | None = None
    transcript_txt: Path | None = None
    filler_report: Path | None = None


ProgressCb = Callable[[str], None]


def analyze(
    video_path: str | Path,
    options: AnalyzeOptions | None = None,
    log: ProgressCb | None = None,
) -> AnalyzeResult:
    """動画を解析し、cuts.json / EDL / SRT などを出力する。"""
    opts = options or AnalyzeOptions()
    emit = log or (lambda msg: None)
    video_path = Path(video_path).resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"ファイルが見つかりません: {video_path}")

    emit(f"素材情報を取得中: {video_path.name}")
    info = media.probe(video_path)
    emit(f"  {info.fps:.3f} fps / {info.duration:.1f} 秒")

    out_dir = Path(opts.output_dir) if opts.output_dir else (
        video_path.parent / f"{video_path.stem}_assist"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    result = AnalyzeResult(info=info, output_dir=out_dir)

    # --- 無音検出 → 発話区間 ---
    if opts.do_silence:
        if not info.has_audio:
            raise RuntimeError("音声トラックがないため無音検出できません。")
        emit(f"無音検出中 (しきい値 {opts.silence.noise_db} dB, "
             f"最小無音 {opts.silence.min_silence} 秒)...")
        result.segments = silence.detect_speech_segments(
            video_path, info.duration, opts.silence
        )
        kept = sum(s.duration for s in result.segments)
        emit(f"  発話区間 {len(result.segments)} 個 / "
             f"残り {kept:.1f} 秒 (元の {info.duration:.1f} 秒から "
             f"{info.duration - kept:.1f} 秒カット)")
    else:
        result.segments = [Segment(0.0, info.duration)]

    # --- 文字起こし (字幕・フィラー検出兼用) ---
    need_transcript = opts.do_subtitles or opts.do_fillers
    if need_transcript:
        from .analysis import transcribe as tr

        emit(f"文字起こし中 (faster-whisper / {opts.whisper_model})... "
             "初回はモデルのダウンロードに時間がかかります")
        with tempfile.TemporaryDirectory() as tmp:
            wav = media.extract_audio(video_path, Path(tmp) / "audio.wav")
            result.transcript = tr.transcribe(
                wav,
                tr.TranscribeOptions(
                    model_size=opts.whisper_model, language=opts.language
                ),
                progress_cb=lambda sec, text: emit(
                    f"  [{sec:6.1f}s / {info.duration:.1f}s] {text}"
                ),
            )
        emit(f"  {len(result.transcript)} セグメントを文字起こししました")

        result.transcript_txt = out_dir / "transcript.txt"
        result.transcript_txt.write_text(
            tr.plain_text(result.transcript), encoding="utf-8"
        )

    # --- フィラー検出 ---
    markers: list[Marker] = []
    if opts.do_fillers and result.transcript:
        from .analysis.transcribe import all_words

        fillers = load_filler_dict(opts.filler_dict_path)
        result.fillers = detect_fillers(all_words(result.transcript), fillers)
        emit(f"フィラー語を {len(result.fillers)} 件検出")
        if opts.cut_fillers and result.fillers:
            before = sum(s.duration for s in result.segments)
            result.segments = subtract_segments(
                result.segments, filler_cut_segments(result.fillers)
            )
            after = sum(s.duration for s in result.segments)
            emit(f"  フィラー自動カット: さらに {before - after:.1f} 秒カット")
        else:
            markers.extend(
                Marker(
                    sec=h.start,
                    name=f"フィラー: {h.text}",
                    note="カット候補",
                    color="Red",
                    duration_sec=h.end - h.start,
                )
                for h in result.fillers
            )
        report = out_dir / "fillers.txt"
        report.write_text(
            "\n".join(
                f"{h.start:8.2f}s - {h.end:8.2f}s  {h.text}" for h in result.fillers
            )
            or "フィラー語は検出されませんでした",
            encoding="utf-8",
        )
        result.filler_report = report

    # --- シーン検出 ---
    if opts.do_scenes:
        from .analysis.scenes import detect_scene_cuts

        emit(f"シーン検出中 (しきい値 {opts.scene_threshold})...")
        result.scene_cuts = detect_scene_cuts(video_path, opts.scene_threshold)
        emit(f"  シーン切り替わり {len(result.scene_cuts)} 箇所")
        markers.extend(
            Marker(sec=c, name="シーン切替", color="Blue") for c in result.scene_cuts
        )

    # --- 字幕出力 ---
    if opts.do_subtitles and result.transcript:
        result.srt_path = srt_mod.write_srt(
            result.transcript,
            out_dir / "subtitles.srt",
            max_chars_per_line=opts.max_chars_per_line,
        )
        emit(f"字幕を出力: {result.srt_path}")

    # --- カットリスト・タイムライン出力 (対象ソフトごと) ---
    targets = opts.targets or {"resolve"}
    cuts = cutlist_mod.build_cutlist(
        info,
        result.segments,
        markers=markers,
        scene_cuts=result.scene_cuts,
        srt_path=result.srt_path,
    )
    result.cuts_json = cutlist_mod.write_cutlist(cuts, out_dir / "cuts.json")
    result.edl_path = edl_mod.write_edl(
        result.segments,
        info.fps,
        out_dir / "timeline.edl",
        title=cuts["timeline_name"],
        clip_name=video_path.name,
    )
    emit(f"カットリストを出力: {result.cuts_json}")
    emit(f"EDL (汎用) を出力: {result.edl_path}")

    if "resolve" in targets:
        pointer = cutlist_mod.write_latest_pointer(result.cuts_json, result.srt_path)
        emit(f"Resolve 用ポインタを更新: {pointer}")
    if "fcp" in targets:
        result.fcpxml_path = fcpxml_mod.write_fcpxml(
            info,
            result.segments,
            out_dir / "timeline.fcpxml",
            markers=markers,
            project_name=cuts["timeline_name"],
        )
        emit(f"Final Cut Pro 用 FCPXML を出力: {result.fcpxml_path}")
    if "premiere" in targets:
        result.premiere_xml_path = xmeml_mod.write_xmeml(
            info,
            result.segments,
            out_dir / "timeline_premiere.xml",
            markers=markers,
            sequence_name=cuts["timeline_name"],
        )
        emit(f"Premiere Pro 用 XML を出力: {result.premiere_xml_path}")

    emit("完了!")
    if "resolve" in targets:
        emit("  Resolve: Workspace > Scripts > ResolveAssist_ApplyCuts を実行")
    if "fcp" in targets:
        emit("  Final Cut Pro: File > Import > XML で timeline.fcpxml を読み込み")
    if "premiere" in targets:
        emit("  Premiere Pro: File > Import で timeline_premiere.xml を読み込み")
    return result
