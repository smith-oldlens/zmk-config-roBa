"""お手本動画からの編集スタイル学習 (style.json) と適用。

「学習」といっても機械学習ではなく、お手本動画を解析して編集の型を
数値プロファイルとして抽出する:

- カットテンポ: ショット長の分布、残されている間(ま)の長さ、発話密度
  → 新しい動画の無音カットパラメータを導出
- 字幕スタイル: 1行文字数・行数・表示時間・読み上げ速度 (SRT優先、なければWhisper推定)
- 構成の型: イントロ/本編/締めの長さ比率 → 新タイムラインへのガイドマーカー
- 音量感: 統合ラウドネス → 新しい動画との差分を提示

抽出できないもの(テロップのデザイン・エフェクト等)はスコープ外。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .analysis.loudness import measure_loudness
from .analysis.silence import SilenceOptions, detect_silences
from .media import probe
from .segments import invert_segments, timeline_time_to_source_time
from .types import Marker, Segment, TranscriptSegment

STYLE_VERSION = 1

ProgressCb = Callable[[str], None]


def _quantile(data: list[float], q: float) -> float:
    """ソート補間による分位点。data が空なら 0。"""
    if not data:
        return 0.0
    s = sorted(data)
    idx = (len(s) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# --- 学習 ---------------------------------------------------------------


def learn_cut_tempo(video_path: str | Path, duration: float) -> dict:
    """お手本に残っている間(ま)とショット長からカットテンポを学習する。

    お手本は編集済みなので、そこに残っている無音 = そのスタイルが許容する
    間の長さ。それより長い無音を新しい動画でカットすれば近いテンポになる。
    """
    # 短い間も拾えるよう検出しきい値は細かめにする
    silences = detect_silences(
        video_path, SilenceOptions(noise_db=-35.0, min_silence=0.1)
    )
    bounded = [Segment(s.start, min(s.end, duration)) for s in silences]
    gaps = [s.duration for s in bounded if 0.05 < s.duration < 5.0]
    speech = invert_segments(bounded, duration)
    speech_ratio = sum(s.duration for s in speech) / duration if duration else 0.0

    gap_median = _quantile(gaps, 0.5)
    gap_p75 = _quantile(gaps, 0.75)

    shot_lengths: list[float] = []
    scene_cuts: list[float] = []
    try:
        from .analysis.scenes import detect_scene_cuts

        scene_cuts = detect_scene_cuts(video_path)
        boundaries = [0.0] + scene_cuts + [duration]
        shot_lengths = [
            b - a for a, b in zip(boundaries, boundaries[1:]) if b - a > 0.2
        ]
    except RuntimeError:
        pass  # scenedetect 未インストールならショット統計は省略

    return {
        "speech_ratio": round(speech_ratio, 3),
        "gap_median_sec": round(gap_median, 3),
        "gap_p75_sec": round(gap_p75, 3),
        "shot_median_sec": round(_quantile(shot_lengths, 0.5), 2) or None,
        "shot_mean_sec": (
            round(sum(shot_lengths) / len(shot_lengths), 2) if shot_lengths else None
        ),
        "scene_cut_count": len(scene_cuts),
        # 新しい動画に適用する無音カットパラメータ (経験則による導出)
        "silence_options": {
            "min_silence": round(_clamp(gap_p75 + 0.05, 0.2, 1.2), 2),
            "pad_before": 0.08,
            "pad_after": round(_clamp(gap_median / 2, 0.05, 0.4), 2),
            "merge_gap": round(_clamp(gap_median, 0.1, 0.5), 2),
        },
        "_scene_cuts": scene_cuts,  # 構成学習用 (プロファイル保存時に除去)
    }


def learn_subtitle_style(
    captions: list[TranscriptSegment], duration: float, source: str
) -> dict:
    """字幕リストから体裁の統計を取る。source は 'srt' か 'whisper'。"""
    lines = [line for cap in captions for line in cap.text.split("\n") if line]
    line_lengths = [float(len(l)) for l in lines]
    lines_per_caption = [float(len(cap.text.split("\n"))) for cap in captions]
    durations = [cap.end - cap.start for cap in captions if cap.end > cap.start]
    total_chars = sum(len(cap.text.replace("\n", "")) for cap in captions)
    total_time = sum(durations)

    if source == "srt":
        # 実際の行構造から正確に学習できる
        max_chars = int(round(_quantile(line_lengths, 0.9))) or 26
    else:
        # Whisper セグメントは行構造を持たないため推定になる
        max_chars = int(round(_clamp(_quantile(line_lengths, 0.5), 12, 30))) or 26

    return {
        "source": source,
        "caption_count": len(captions),
        "max_chars_per_line": int(_clamp(max_chars, 8, 40)),
        "max_lines": int(_clamp(max(lines_per_caption, default=2), 1, 3)),
        "median_duration_sec": round(_quantile(durations, 0.5), 2),
        "min_duration_sec": round(_clamp(_quantile(durations, 0.1), 0.4, 2.0), 2),
        "chars_per_sec": round(total_chars / total_time, 2) if total_time else None,
        "coverage_ratio": round(total_time / duration, 3) if duration else None,
    }


def learn_structure(
    duration: float, first_speech_sec: float, scene_cuts: list[float]
) -> dict:
    """イントロ/本編/締めのおおまかな比率を推定する (目安レベル)。"""
    cap = 0.2 * duration
    intro_end = next(
        (c for c in scene_cuts if c > first_speech_sec + 1.0), None
    )
    if intro_end is None:
        intro_end = first_speech_sec + 0.05 * duration
    intro_end = _clamp(intro_end, 0.0, cap)

    last_cut = next(
        (c for c in reversed(scene_cuts) if c < duration - 1.0), None
    )
    outro_len = _clamp(duration - last_cut, 0.0, cap) if last_cut else 0.05 * duration

    return {
        "intro_ratio": round(intro_end / duration, 3) if duration else 0.0,
        "outro_ratio": round(outro_len / duration, 3) if duration else 0.0,
    }


def learn_style(
    video_path: str | Path,
    srt_path: str | Path | None = None,
    whisper_model: str | None = "small",
    language: str = "ja",
    log: ProgressCb | None = None,
) -> dict:
    """お手本動画からスタイルプロファイルを生成する。"""
    emit = log or (lambda msg: None)
    video_path = Path(video_path).resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"ファイルが見つかりません: {video_path}")

    emit(f"お手本を解析中: {video_path.name}")
    info = probe(video_path)

    emit("カットテンポを学習中 (間の長さ・ショット長)...")
    tempo = learn_cut_tempo(video_path, info.duration)
    scene_cuts = tempo.pop("_scene_cuts")
    emit(
        f"  発話密度 {tempo['speech_ratio']:.0%} / "
        f"残されている間の中央値 {tempo['gap_median_sec']:.2f} 秒"
        + (
            f" / ショット長中央値 {tempo['shot_median_sec']} 秒"
            if tempo["shot_median_sec"]
            else ""
        )
    )

    # 字幕スタイル: SRT 優先、なければ Whisper 推定
    subtitles = None
    captions: list[TranscriptSegment] = []
    if srt_path:
        from .export.srt import parse_srt

        captions = parse_srt(Path(srt_path).read_text(encoding="utf-8"))
        if captions:
            subtitles = learn_subtitle_style(captions, info.duration, "srt")
            emit(f"字幕スタイルを SRT から学習: {len(captions)} 枚")
    elif whisper_model:
        try:
            import tempfile

            from .analysis import transcribe as tr
            from .media import extract_audio

            emit(f"字幕スタイルを Whisper で推定中 (モデル {whisper_model})...")
            with tempfile.TemporaryDirectory() as tmp:
                wav = extract_audio(video_path, Path(tmp) / "audio.wav")
                captions = tr.transcribe(
                    wav,
                    tr.TranscribeOptions(model_size=whisper_model, language=language),
                )
            if captions:
                subtitles = learn_subtitle_style(captions, info.duration, "whisper")
                emit(f"  {len(captions)} セグメントから推定 (体裁は概算)")
        except RuntimeError as e:
            emit(f"  字幕スタイルはスキップ: {e}")

    first_speech = 0.0
    if captions:
        first_speech = captions[0].start
    structure = learn_structure(info.duration, first_speech, scene_cuts)
    emit(
        f"構成: イントロ {structure['intro_ratio']:.0%} / "
        f"締め {structure['outro_ratio']:.0%} (目安)"
    )

    emit("音量を計測中...")
    loudness = measure_loudness(video_path)
    if loudness:
        emit(f"  統合ラウドネス {loudness['integrated_lufs']:.1f} LUFS")

    return {
        "version": STYLE_VERSION,
        "source": str(video_path),
        "duration_sec": info.duration,
        "cut_tempo": tempo,
        "subtitles": subtitles,
        "structure": structure,
        "loudness": loudness,
    }


def save_style(profile: dict, path: str | Path) -> Path:
    path = Path(path)
    path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def load_style(path: str | Path) -> dict:
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    if profile.get("version") != STYLE_VERSION:
        raise ValueError(f"未対応の style.json バージョンです: {profile.get('version')}")
    return profile


# --- 適用 ---------------------------------------------------------------


def silence_options_from_style(profile: dict) -> dict:
    """プロファイルから無音カットパラメータ (dict) を取り出す。"""
    return dict((profile.get("cut_tempo") or {}).get("silence_options") or {})


def structure_guide_markers(profile: dict, segments: list[Segment]) -> list[Marker]:
    """構成の型をカット後タイムライン相当の位置のガイドマーカーにする。"""
    structure = profile.get("structure") or {}
    intro_ratio = structure.get("intro_ratio") or 0.0
    outro_ratio = structure.get("outro_ratio") or 0.0
    kept = sum(s.duration for s in segments)
    markers: list[Marker] = []
    points = []
    if 0.0 < intro_ratio < 1.0:
        points.append((kept * intro_ratio, "構成ガイド: イントロここまで (お手本比)"))
    if 0.0 < outro_ratio < 1.0:
        points.append((kept * (1.0 - outro_ratio), "構成ガイド: 締めに入る目安 (お手本比)"))
    for t, name in points:
        src = timeline_time_to_source_time(t, segments)
        if src is not None:
            markers.append(Marker(sec=src, name=name, color="Cyan"))
    return markers


def loudness_report(
    profile: dict, video_path: str | Path, log: ProgressCb | None = None
) -> str | None:
    """お手本と新しい動画のラウドネス差を比較したレポート文字列を返す。"""
    emit = log or (lambda msg: None)
    ref = profile.get("loudness")
    if not ref:
        return None
    emit("音量をお手本と比較中...")
    current = measure_loudness(video_path)
    if not current:
        return None
    delta = ref["integrated_lufs"] - current["integrated_lufs"]
    lines = [
        "== 音量比較 (Resolve Assist スタイル適用) ==",
        f"お手本:     {ref['integrated_lufs']:.1f} LUFS (ピーク {ref['true_peak_db']:.1f} dBTP)",
        f"この動画:   {current['integrated_lufs']:.1f} LUFS (ピーク {current['true_peak_db']:.1f} dBTP)",
    ]
    if abs(delta) < 1.0:
        lines.append("→ 音量感はほぼ同じです。調整は不要です。")
    else:
        direction = "上げる" if delta > 0 else "下げる"
        lines.append(
            f"→ クリップゲインを約 {abs(delta):.1f} dB {direction}と、お手本の音量感に近づきます。"
        )
    report = "\n".join(lines)
    emit("  " + lines[-1].lstrip("→ "))
    return report
