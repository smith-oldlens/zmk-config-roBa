"""CLI エントリポイント。GUI と同じパイプラインをターミナルから使う。

例:
  resolve-assist analyze talk.mp4                      # 無音カットのみ
  resolve-assist analyze talk.mp4 --subtitles --fillers
  resolve-assist analyze talk.mp4 --subtitles --model medium --cut-fillers
  resolve-assist learn ohandbook.mp4 --srt ohandbook.srt   # お手本から型を学習
  resolve-assist analyze talk.mp4 --style ohandbook_style.json --subtitles
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analysis.silence import SilenceOptions
from .pipeline import AnalyzeOptions, analyze

# --style での上書き判定に使う既定値 (add_argument と同じ値を保つこと)
_DEFAULTS = {
    "min_silence": 0.35,
    "pad_before": 0.10,
    "pad_after": 0.15,
    "max_chars": 26,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="resolve-assist",
        description="DaVinci Resolve 編集補助: 無音カット・フィラー検出・シーン検出・字幕生成",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="動画を解析して cuts.json / EDL / SRT を出力する")
    p.add_argument("video", help="対象の動画ファイル")
    p.add_argument("-o", "--output-dir", help="出力先フォルダ (既定: <動画名>_assist/)")
    p.add_argument("--target", action="append", dest="targets",
                   choices=["resolve", "fcp", "premiere", "all"],
                   help="対象の編集ソフト (複数指定可, 既定: resolve)。"
                        "fcp=Final Cut Pro, premiere=Premiere Pro, all=全部")

    p.add_argument("--no-silence", action="store_true", help="無音カットを行わない")
    p.add_argument("--subtitles", action="store_true", help="字幕 (SRT) を生成する")
    p.add_argument("--fillers", action="store_true", help="フィラー語を検出する")
    p.add_argument("--cut-fillers", action="store_true",
                   help="検出したフィラーを自動カットに含める (既定はマーカー提示のみ)")
    p.add_argument("--scenes", action="store_true", help="シーン検出を行う")

    p.add_argument("--silence-db", type=float, default=-35.0,
                   help="無音判定しきい値 dB (既定 -35)")
    p.add_argument("--min-silence", type=float, default=0.35,
                   help="カット対象とする最小無音長 秒 (既定 0.35)")
    p.add_argument("--pad-before", type=float, default=0.10,
                   help="発話区間の頭に残すマージン 秒 (既定 0.10)")
    p.add_argument("--pad-after", type=float, default=0.15,
                   help="発話区間の尻に残すマージン 秒 (既定 0.15)")
    p.add_argument("--min-clip", type=float, default=0.30,
                   help="これ未満の発話クリップは捨てる 秒 (既定 0.30)")

    p.add_argument("--model", default="small",
                   help="Whisper モデル (tiny/base/small/medium/large-v3, 既定 small)")
    p.add_argument("--language", default="ja", help="文字起こし言語 (既定 ja)")
    p.add_argument("--max-chars", type=int, default=26,
                   help="字幕の1行最大文字数 (既定 26)")
    p.add_argument("--filler-dict", help="フィラー辞書ファイル (1行1語 or JSON配列)")
    p.add_argument("--scene-threshold", type=float, default=27.0,
                   help="シーン検出しきい値 (既定 27)")
    p.add_argument("--style", help="learn で生成した style.json を適用する")

    lp = sub.add_parser(
        "learn",
        help="お手本動画から編集スタイル (テンポ・字幕体裁・構成・音量) を学習して style.json を作る",
    )
    lp.add_argument("video", help="お手本の動画ファイル")
    lp.add_argument("--srt", help="お手本の字幕ファイル (あれば体裁を正確に学習できる)")
    lp.add_argument("-o", "--output",
                    help="出力先 (既定: <お手本名>_style.json)")
    lp.add_argument("--model", default="small",
                    help="SRTがない場合の Whisper モデル (既定 small)")
    lp.add_argument("--no-transcribe", action="store_true",
                    help="SRTがない場合でも Whisper 推定を行わない")
    lp.add_argument("--language", default="ja", help="文字起こし言語 (既定 ja)")
    return parser


def _run_learn(args) -> int:
    from .style import learn_style, save_style

    try:
        profile = learn_style(
            args.video,
            srt_path=args.srt,
            whisper_model=None if args.no_transcribe else args.model,
            language=args.language,
            log=print,
        )
        out = args.output or str(
            Path(args.video).resolve().with_name(f"{Path(args.video).stem}_style.json")
        )
        save_style(profile, out)
        print(f"スタイルプロファイルを保存: {out}")
        print("適用するには: resolve-assist analyze <動画> --style " + out)
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    return 0


def _apply_style_to_args(args, profile: dict) -> None:
    """CLI で明示指定されていないパラメータをスタイル値で上書きする。"""
    from .style import silence_options_from_style

    sil = silence_options_from_style(profile)
    mapping = {
        "min_silence": sil.get("min_silence"),
        "pad_before": sil.get("pad_before"),
        "pad_after": sil.get("pad_after"),
        "max_chars": (profile.get("subtitles") or {}).get("max_chars_per_line"),
    }
    for name, style_value in mapping.items():
        if style_value is not None and getattr(args, name) == _DEFAULTS[name]:
            setattr(args, name, style_value)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "learn":
        return _run_learn(args)
    if args.command != "analyze":
        return 2

    if args.cut_fillers:
        args.fillers = True

    profile = None
    if args.style:
        from .style import load_style

        try:
            profile = load_style(args.style)
        except (OSError, ValueError) as e:
            print(f"エラー: style.json を読めません: {e}", file=sys.stderr)
            return 1
        _apply_style_to_args(args, profile)
        print(f"スタイルを適用: {args.style}")

    targets = set(args.targets or ["resolve"])
    if "all" in targets:
        targets = {"resolve", "fcp", "premiere"}

    style_subs = (profile.get("subtitles") or {}) if profile else {}
    merge_gap = None
    if profile:
        from .style import silence_options_from_style

        merge_gap = silence_options_from_style(profile).get("merge_gap")

    options = AnalyzeOptions(
        do_silence=not args.no_silence,
        do_subtitles=args.subtitles,
        do_fillers=args.fillers,
        do_scenes=args.scenes,
        cut_fillers=args.cut_fillers,
        silence=SilenceOptions(
            noise_db=args.silence_db,
            min_silence=args.min_silence,
            pad_before=args.pad_before,
            pad_after=args.pad_after,
            min_clip=args.min_clip,
            **({"merge_gap": merge_gap} if merge_gap is not None else {}),
        ),
        whisper_model=args.model,
        language=args.language,
        scene_threshold=args.scene_threshold,
        filler_dict_path=args.filler_dict,
        max_chars_per_line=args.max_chars,
        srt_max_lines=int(style_subs.get("max_lines") or 2),
        srt_min_duration=float(style_subs.get("min_duration_sec") or 0.0),
        style=profile,
        targets=targets,
        output_dir=args.output_dir,
    )
    try:
        analyze(args.video, options, log=print)
    except Exception as e:  # ユーザー向けに簡潔なエラー表示にする
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
