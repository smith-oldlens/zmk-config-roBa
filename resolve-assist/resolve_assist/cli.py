"""CLI エントリポイント。GUI と同じパイプラインをターミナルから使う。

例:
  resolve-assist analyze talk.mp4                      # 無音カットのみ
  resolve-assist analyze talk.mp4 --subtitles --fillers
  resolve-assist analyze talk.mp4 --subtitles --model medium --cut-fillers
"""

from __future__ import annotations

import argparse
import sys

from .analysis.silence import SilenceOptions
from .pipeline import AnalyzeOptions, analyze


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "analyze":
        return 2

    if args.cut_fillers:
        args.fillers = True

    targets = set(args.targets or ["resolve"])
    if "all" in targets:
        targets = {"resolve", "fcp", "premiere"}

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
        ),
        whisper_model=args.model,
        language=args.language,
        scene_threshold=args.scene_threshold,
        filler_dict_path=args.filler_dict,
        max_chars_per_line=args.max_chars,
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
