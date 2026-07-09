"""Resolve Assist の簡易 GUI (tkinter)。

起動:  resolve-assist-gui  または  python -m resolve_assist.gui.app
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..analysis.silence import SilenceOptions
from ..pipeline import AnalyzeOptions, AnalyzeResult, analyze

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Resolve Assist — カット & 字幕補助")
        root.geometry("780x800")
        root.minsize(660, 680)

        self.video_path = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.do_silence = tk.BooleanVar(value=True)
        self.use_vad = tk.BooleanVar(value=False)
        self.do_subtitles = tk.BooleanVar(value=True)
        self.do_fillers = tk.BooleanVar(value=True)
        self.cut_fillers = tk.BooleanVar(value=False)
        self.do_scenes = tk.BooleanVar(value=False)
        self.target_resolve = tk.BooleanVar(value=True)
        self.target_fcp = tk.BooleanVar(value=False)
        self.target_premiere = tk.BooleanVar(value=False)
        self.style_path = tk.StringVar()
        self.silence_db = tk.DoubleVar(value=-35.0)
        self.min_silence = tk.DoubleVar(value=0.35)
        self.pad_before = tk.DoubleVar(value=0.10)
        self.pad_after = tk.DoubleVar(value=0.15)
        self.whisper_model = tk.StringVar(value="small")
        self.shorts_count = tk.IntVar(value=3)
        self.shorts_max_duration = tk.DoubleVar(value=60.0)
        self.shorts_fit = tk.StringVar(value="blur")
        self.shorts_burn = tk.BooleanVar(value=True)
        self.shorts_ranges = tk.StringVar()

        self._log_queue: queue.Queue[str] = queue.Queue()
        self._running = False
        self._result: AnalyzeResult | None = None
        self._last_output_dir: str | None = None

        self._build_widgets()
        self._poll_log_queue()

    # --- UI 構築 -------------------------------------------------------

    def _build_widgets(self):
        pad = {"padx": 8, "pady": 4}
        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, **pad)

        # ファイル選択
        file_row = ttk.Frame(frame)
        file_row.pack(fill="x", **pad)
        ttk.Label(file_row, text="動画ファイル:").pack(side="left")
        ttk.Entry(file_row, textvariable=self.video_path).pack(
            side="left", fill="x", expand=True, padx=6
        )
        ttk.Button(file_row, text="選択...", command=self._choose_video).pack(side="left")

        out_row = ttk.Frame(frame)
        out_row.pack(fill="x", **pad)
        ttk.Label(out_row, text="出力フォルダ:").pack(side="left")
        ttk.Entry(out_row, textvariable=self.output_dir).pack(
            side="left", fill="x", expand=True, padx=6
        )
        ttk.Button(out_row, text="選択...", command=self._choose_output).pack(side="left")
        ttk.Label(frame, text="(出力フォルダ未指定の場合は <動画名>_assist/ に出力)").pack(
            anchor="w", padx=8
        )

        # タブ: カット&字幕 / ショート動画
        notebook = ttk.Notebook(frame)
        notebook.pack(fill="x", **pad)
        tab1 = ttk.Frame(notebook)
        tab2 = ttk.Frame(notebook)
        notebook.add(tab1, text=" カット & 字幕 ")
        notebook.add(tab2, text=" ショート動画 (Shorts/Reels) ")

        # 機能の選択
        feat = ttk.LabelFrame(tab1, text="実行する処理")
        feat.pack(fill="x", **pad)
        ttk.Checkbutton(feat, text="無音部分の自動カット", variable=self.do_silence).grid(
            row=0, column=0, sticky="w", padx=8, pady=2
        )
        ttk.Checkbutton(
            feat, text="VADで精密カット (咳払い・環境音を除去)", variable=self.use_vad
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=2)
        ttk.Checkbutton(feat, text="字幕生成 (SRT)", variable=self.do_subtitles).grid(
            row=0, column=1, sticky="w", padx=8, pady=2
        )
        ttk.Checkbutton(feat, text="フィラー語の検出", variable=self.do_fillers).grid(
            row=1, column=0, sticky="w", padx=8, pady=2
        )
        ttk.Checkbutton(
            feat, text="フィラーも自動カットする (注意: 誤検出あり)", variable=self.cut_fillers
        ).grid(row=1, column=1, sticky="w", padx=8, pady=2)
        ttk.Checkbutton(feat, text="シーン検出 (マーカー)", variable=self.do_scenes).grid(
            row=2, column=0, sticky="w", padx=8, pady=2
        )

        # 出力先の編集ソフト
        target = ttk.LabelFrame(tab1, text="対象の編集ソフト")
        target.pack(fill="x", **pad)
        ttk.Checkbutton(
            target, text="DaVinci Resolve", variable=self.target_resolve
        ).grid(row=0, column=0, sticky="w", padx=8, pady=2)
        ttk.Checkbutton(
            target, text="Final Cut Pro (FCPXML)", variable=self.target_fcp
        ).grid(row=0, column=1, sticky="w", padx=8, pady=2)
        ttk.Checkbutton(
            target, text="Premiere Pro (XML)", variable=self.target_premiere
        ).grid(row=0, column=2, sticky="w", padx=8, pady=2)

        # スタイル学習
        style_frame = ttk.LabelFrame(tab1, text="お手本スタイル (任意)")
        style_frame.pack(fill="x", **pad)
        style_row = ttk.Frame(style_frame)
        style_row.pack(fill="x", padx=8, pady=4)
        ttk.Entry(style_row, textvariable=self.style_path).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(style_row, text="選択...", command=self._choose_style).pack(
            side="left", padx=4
        )
        ttk.Button(
            style_row, text="お手本から学習...", command=self._learn_style
        ).pack(side="left")
        ttk.Label(
            style_frame,
            text="お手本動画から学習した style.json を指定すると、"
            "カットのテンポ・字幕体裁・構成・音量感をお手本に合わせます",
        ).pack(anchor="w", padx=8, pady=(0, 4))

        # パラメータ
        params = ttk.LabelFrame(tab1, text="パラメータ")
        params.pack(fill="x", **pad)
        self._param_entry(params, 0, 0, "無音しきい値 (dB)", self.silence_db)
        self._param_entry(params, 0, 2, "最小無音長 (秒)", self.min_silence)
        self._param_entry(params, 1, 0, "頭マージン (秒)", self.pad_before)
        self._param_entry(params, 1, 2, "尻マージン (秒)", self.pad_after)
        ttk.Label(params, text="Whisper モデル").grid(row=2, column=0, sticky="e", padx=6, pady=2)
        ttk.Combobox(
            params, textvariable=self.whisper_model, values=WHISPER_MODELS,
            state="readonly", width=10,
        ).grid(row=2, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(params, text="(small 推奨。精度重視なら medium)").grid(
            row=2, column=2, columnspan=2, sticky="w", padx=6
        )

        # 実行ボタン類 (カット&字幕)
        buttons = ttk.Frame(tab1)
        buttons.pack(fill="x", **pad)
        self.run_button = ttk.Button(buttons, text="解析実行", command=self._run)
        self.run_button.pack(side="left")
        self.apply_button = ttk.Button(
            buttons,
            text="Resolveへ直接適用 (Studio)",
            command=self._apply_to_resolve,
            state="disabled",
        )
        self.apply_button.pack(side="left", padx=6)

        # --- ショート動画タブ ---
        s_opts = ttk.LabelFrame(tab2, text="生成設定")
        s_opts.pack(fill="x", **pad)
        self._param_entry(s_opts, 0, 0, "本数 (自動提案)", self.shorts_count)
        self._param_entry(s_opts, 0, 2, "上限尺 (秒)", self.shorts_max_duration)
        ttk.Label(s_opts, text="(Shorts=60 / Reels=90)").grid(
            row=0, column=4, sticky="w", padx=6
        )
        ttk.Label(s_opts, text="縦型変換").grid(row=1, column=0, sticky="e", padx=6, pady=2)
        fit_row = ttk.Frame(s_opts)
        fit_row.grid(row=1, column=1, columnspan=3, sticky="w")
        ttk.Radiobutton(
            fit_row, text="ぼかし背景", variable=self.shorts_fit, value="blur"
        ).pack(side="left", padx=4)
        ttk.Radiobutton(
            fit_row, text="中央クロップ", variable=self.shorts_fit, value="crop"
        ).pack(side="left", padx=4)
        ttk.Checkbutton(
            s_opts, text="字幕を焼き込む (大きめ白文字+黒縁)", variable=self.shorts_burn
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=8, pady=2)
        range_row = ttk.Frame(s_opts)
        range_row.grid(row=3, column=0, columnspan=5, sticky="we", padx=6, pady=2)
        ttk.Label(range_row, text="区間を手動指定 (任意):").pack(side="left")
        ttk.Entry(range_row, textvariable=self.shorts_ranges).pack(
            side="left", fill="x", expand=True, padx=6
        )
        ttk.Label(
            s_opts,
            text="例: 2:15-3:10, 5:00-5:45 (カンマ区切り。空欄なら見どころを自動提案)",
        ).grid(row=4, column=0, columnspan=5, sticky="w", padx=8)

        s_buttons = ttk.Frame(tab2)
        s_buttons.pack(fill="x", **pad)
        self.shorts_button = ttk.Button(
            s_buttons, text="ショート生成", command=self._run_shorts
        )
        self.shorts_button.pack(side="left")

        # 共通: 出力フォルダを開く / 進捗 / ログ
        common_row = ttk.Frame(frame)
        common_row.pack(fill="x", **pad)
        self.open_button = ttk.Button(
            common_row, text="出力フォルダを開く", command=self._open_output,
            state="disabled",
        )
        self.open_button.pack(side="left")

        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x", **pad)

        # ログ
        log_frame = ttk.LabelFrame(frame, text="ログ")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, height=12, state="disabled", wrap="word")
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)

    def _param_entry(self, parent, row, col, label, var):
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="e", padx=6, pady=2)
        ttk.Entry(parent, textvariable=var, width=8).grid(
            row=row, column=col + 1, sticky="w", padx=6, pady=2
        )

    # --- イベント -------------------------------------------------------

    def _choose_video(self):
        path = filedialog.askopenfilename(
            title="動画ファイルを選択",
            filetypes=[
                ("動画/音声", "*.mp4 *.mov *.mkv *.avi *.m4v *.wav *.mp3 *.m4a"),
                ("すべて", "*"),
            ],
        )
        if path:
            self.video_path.set(path)

    def _choose_output(self):
        path = filedialog.askdirectory(title="出力フォルダを選択")
        if path:
            self.output_dir.set(path)

    def _choose_style(self):
        path = filedialog.askopenfilename(
            title="style.json を選択",
            filetypes=[("スタイルプロファイル", "*.json"), ("すべて", "*")],
        )
        if path:
            self.style_path.set(path)
            self._load_style_into_fields(path)

    def _load_style_into_fields(self, path: str):
        """スタイルの無音カットパラメータを入力欄に反映する (ユーザーが微調整可能)。"""
        try:
            from ..style import load_style, silence_options_from_style

            profile = load_style(path)
        except (OSError, ValueError) as e:
            messagebox.showerror("Resolve Assist", f"style.json を読めません:\n{e}")
            self.style_path.set("")
            return
        sil = silence_options_from_style(profile)
        if sil.get("min_silence") is not None:
            self.min_silence.set(sil["min_silence"])
        if sil.get("pad_before") is not None:
            self.pad_before.set(sil["pad_before"])
        if sil.get("pad_after") is not None:
            self.pad_after.set(sil["pad_after"])
        self._log(f"スタイルを読み込み、パラメータに反映しました: {path}")

    def _learn_style(self):
        if self._running:
            return
        video = filedialog.askopenfilename(
            title="お手本の動画を選択",
            filetypes=[
                ("動画/音声", "*.mp4 *.mov *.mkv *.avi *.m4v *.wav *.mp3 *.m4a"),
                ("すべて", "*"),
            ],
        )
        if not video:
            return
        srt = filedialog.askopenfilename(
            title="お手本の字幕 SRT (あれば選択、なければキャンセル)",
            filetypes=[("SRT字幕", "*.srt"), ("すべて", "*")],
        ) or None

        self._running = True
        self.run_button.configure(state="disabled")
        self.progress.start(12)
        self._log("=" * 60)
        self._log("お手本からスタイルを学習します...")

        def worker():
            try:
                from ..style import learn_style, save_style

                profile = learn_style(
                    video,
                    srt_path=srt,
                    whisper_model=self.whisper_model.get(),
                    log=self._log,
                )
                out = str(Path(video).with_name(f"{Path(video).stem}_style.json"))
                save_style(profile, out)
                self._log(f"スタイルプロファイルを保存: {out}")
                self.root.after(0, lambda: self._on_style_learned(out))
            except Exception as e:
                self._log(f"エラー: {e}")
                self.root.after(0, self._on_done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_style_learned(self, style_path: str):
        self._on_done()
        self.style_path.set(style_path)
        self._load_style_into_fields(style_path)

    def _log(self, msg: str):
        self._log_queue.put(msg)

    def _poll_log_queue(self):
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)

    def _run(self):
        if self._running:
            return
        video = self.video_path.get().strip()
        if not video:
            messagebox.showwarning("Resolve Assist", "動画ファイルを選択してください。")
            return
        if not Path(video).exists():
            messagebox.showerror("Resolve Assist", f"ファイルが見つかりません:\n{video}")
            return

        targets = set()
        if self.target_resolve.get():
            targets.add("resolve")
        if self.target_fcp.get():
            targets.add("fcp")
        if self.target_premiere.get():
            targets.add("premiere")
        if not targets:
            messagebox.showwarning(
                "Resolve Assist", "対象の編集ソフトを1つ以上選んでください。"
            )
            return

        profile = None
        style_path = self.style_path.get().strip()
        if style_path:
            try:
                from ..style import load_style

                profile = load_style(style_path)
            except (OSError, ValueError) as e:
                messagebox.showerror(
                    "Resolve Assist", f"style.json を読めません:\n{e}"
                )
                return
        style_subs = (profile.get("subtitles") or {}) if profile else {}

        options = AnalyzeOptions(
            do_silence=self.do_silence.get(),
            use_vad=self.use_vad.get(),
            do_subtitles=self.do_subtitles.get(),
            do_fillers=self.do_fillers.get() or self.cut_fillers.get(),
            do_scenes=self.do_scenes.get(),
            cut_fillers=self.cut_fillers.get(),
            silence=SilenceOptions(
                noise_db=self.silence_db.get(),
                min_silence=self.min_silence.get(),
                pad_before=self.pad_before.get(),
                pad_after=self.pad_after.get(),
            ),
            whisper_model=self.whisper_model.get(),
            max_chars_per_line=int(style_subs.get("max_chars_per_line") or 26),
            srt_max_lines=int(style_subs.get("max_lines") or 2),
            srt_min_duration=float(style_subs.get("min_duration_sec") or 0.0),
            style=profile,
            targets=targets,
            output_dir=self.output_dir.get().strip() or None,
        )

        self._running = True
        self.run_button.configure(state="disabled")
        self.apply_button.configure(state="disabled")
        self.progress.start(12)
        self._log("=" * 60)

        def worker():
            try:
                result = analyze(video, options, log=self._log)
                self._result = result
                self.root.after(0, self._on_success)
            except Exception as e:
                self._log(f"エラー: {e}")
                self.root.after(0, self._on_done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_success(self):
        self._on_done()
        if self._result is not None:
            self._last_output_dir = str(self._result.output_dir)
        self.open_button.configure(state="normal")
        # Studio 版で Resolve が起動していれば直接適用を有効化
        try:
            from .. import resolve_api

            if resolve_api.get_resolve() is not None:
                self.apply_button.configure(state="normal")
                self._log("Resolve (Studio) への接続を確認。直接適用が使えます。")
            else:
                self._log(
                    "Resolve への外部接続は不可 (無償版のため)。"
                    "Resolve 内の Workspace > Scripts から適用してください。"
                )
        except Exception:
            pass

    def _on_done(self):
        self._running = False
        self.progress.stop()
        self.run_button.configure(state="normal")
        self.shorts_button.configure(state="normal")

    def _open_output(self):
        if not self._last_output_dir:
            return
        folder = self._last_output_dir
        if sys.platform == "darwin":
            subprocess.run(["open", folder])
        elif sys.platform.startswith("win"):
            subprocess.run(["explorer", folder])
        else:
            subprocess.run(["xdg-open", folder])

    def _run_shorts(self):
        if self._running:
            return
        video = self.video_path.get().strip()
        if not video:
            messagebox.showwarning("Resolve Assist", "動画ファイルを選択してください。")
            return
        if not Path(video).exists():
            messagebox.showerror("Resolve Assist", f"ファイルが見つかりません:\n{video}")
            return

        from ..cli import parse_range
        from ..shorts import ShortsOptions, make_shorts

        ranges = None
        raw = self.shorts_ranges.get().strip()
        if raw:
            try:
                ranges = [parse_range(r.strip()) for r in raw.split(",") if r.strip()]
            except ValueError as e:
                messagebox.showerror("Resolve Assist", str(e))
                return

        options = ShortsOptions(
            count=self.shorts_count.get(),
            max_duration=self.shorts_max_duration.get(),
            fit=self.shorts_fit.get(),
            burn_subtitles=self.shorts_burn.get(),
            ranges=ranges,
            silence=SilenceOptions(noise_db=self.silence_db.get()),
            whisper_model=self.whisper_model.get(),
            output_dir=self.output_dir.get().strip() or None,
        )

        self._running = True
        self.run_button.configure(state="disabled")
        self.shorts_button.configure(state="disabled")
        self.progress.start(12)
        self._log("=" * 60)

        def worker():
            try:
                result = make_shorts(video, options, log=self._log)
                self._last_output_dir = str(result.output_dir)
                self.root.after(0, self._on_shorts_success)
            except Exception as e:
                self._log(f"エラー: {e}")
                self.root.after(0, self._on_done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_shorts_success(self):
        self._on_done()
        self.open_button.configure(state="normal")

    def _apply_to_resolve(self):
        if self._result is None or self._result.cuts_json is None:
            return
        from ..export.cutlist import read_cutlist
        from .. import resolve_api

        resolve = resolve_api.get_resolve()
        if resolve is None:
            messagebox.showinfo(
                "Resolve Assist",
                "Resolve に接続できませんでした。\n"
                "外部からの接続は Studio 版のみ対応です。無償版では\n"
                "Resolve 内の Workspace > Scripts > ResolveAssist_ApplyCuts を使ってください。",
            )
            return
        cuts = read_cutlist(self._result.cuts_json)
        ok = resolve_api.apply_cuts_to_resolve(resolve, cuts, log=self._log)
        if ok:
            self._log("Resolve への適用が完了しました。")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
