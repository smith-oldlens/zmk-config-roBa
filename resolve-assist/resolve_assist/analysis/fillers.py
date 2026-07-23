"""フィラー語(えー、あの、なんか等)の検出。

Whisper の単語タイムスタンプに対して日本語フィラー辞書を照合する。
誤爆(「あの人」の「あの」など)を完全には避けられないため、既定では
カットせずマーカー/レポートとして提示し、自動カットはオプションにする。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..types import Segment, Word

# 既定の日本語フィラー辞書。単語トークン全体と一致した場合のみヒットとする。
DEFAULT_FILLERS: list[str] = [
    "えー", "えーと", "えーっと", "ええと", "えっと", "えと",
    "あー", "あのー", "うー", "うーん", "んー", "んーと",
    "まあ", "まぁ", "なんか", "そのー", "ですね",
]

# 照合前の正規化: 記号・空白を除去し、長音のゆれを揃える
_STRIP_RE = re.compile(r"[\s、。,.!?!?・…‥「」『』()()]+")


def normalize_token(text: str) -> str:
    text = _STRIP_RE.sub("", text)
    text = text.replace("〜", "ー").replace("~", "ー")
    return text


@dataclass
class FillerHit:
    """検出されたフィラー1件。"""

    start: float
    end: float
    text: str


def load_filler_dict(path: str | Path | None) -> list[str]:
    """フィラー辞書を読み込む。JSON の文字列配列、または1行1語のテキスト。"""
    if path is None:
        return list(DEFAULT_FILLERS)
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(w) for w in data]
    except json.JSONDecodeError:
        pass
    return [line.strip() for line in raw.splitlines() if line.strip() and not line.startswith("#")]


def detect_fillers(
    words: list[Word], fillers: list[str] | None = None
) -> list[FillerHit]:
    """単語列からフィラーを検出する。

    Whisper は「えーっと、」のように句読点込みでトークンを返すことがある
    ため、正規化してから完全一致で照合する。連続するヒットは呼び出し側で
    区間として結合できるよう個別に返す。
    """
    dictionary = {normalize_token(f) for f in (fillers or DEFAULT_FILLERS)}
    dictionary.discard("")
    hits: list[FillerHit] = []
    for w in words:
        if normalize_token(w.text) in dictionary:
            hits.append(FillerHit(start=w.start, end=w.end, text=w.text.strip()))
    return hits


def filler_cut_segments(hits: list[FillerHit], pad: float = 0.04) -> list[Segment]:
    """フィラーヒットを自動カット用の除去区間に変換する。

    pad は単語タイムスタンプの誤差を吸収するための前後マージン。
    """
    return [Segment(max(0.0, h.start - pad), h.end + pad) for h in hits]
