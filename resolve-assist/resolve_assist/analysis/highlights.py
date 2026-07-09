"""ショート動画向けハイライト区間の自動抽出。

文字起こしセグメントを「情報密度(話速)・キーワード・感嘆」でスコアリングし、
無音カット後の実尺が上限に収まる連続窓の中からスコア上位を選ぶ。
機械学習ではなくヒューリスティックなので、あくまで「候補の提案」として使い、
最終判断は人が行う前提。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..types import Segment, TranscriptSegment

# レビュー・解説動画で「見どころ」になりやすい語 (加点対象)
DEFAULT_KEYWORDS: list[str] = [
    "おすすめ", "オススメ", "ポイント", "実は", "重要", "注意", "最強", "最高",
    "一番", "結論", "まとめ", "コツ", "裏技", "比較", "結果", "検証", "ベスト",
    "驚", "すごい", "すごく", "神", "失敗", "問題", "メリット", "デメリット",
    "買って", "良かった", "悪かった", "正直", "本音", "レビュー",
]

_EMPHASIS_RE = re.compile(r"[!?!?]")


@dataclass
class Highlight:
    """ハイライト候補 1 つ。時刻はソース基準の秒。"""

    start: float
    end: float
    score: float
    kept_duration: float          # 無音カット後の実尺 (秒)
    preview: str = ""


def score_text(text: str, keywords: list[str] | None = None) -> float:
    """テキスト単体の見どころスコア。"""
    words = keywords or DEFAULT_KEYWORDS
    score = 0.0
    for kw in words:
        if kw in text:
            score += 2.0
    score += min(len(_EMPHASIS_RE.findall(text)), 3) * 1.0
    return score


def _segment_scores(
    transcript: list[TranscriptSegment], keywords: list[str] | None
) -> list[float]:
    scores = []
    for seg in transcript:
        dur = max(seg.end - seg.start, 0.1)
        chars_per_sec = len(seg.text) / dur
        # 話速 (情報密度) は 8字/秒 で頭打ちの加点
        scores.append(min(chars_per_sec / 8.0, 1.0) + score_text(seg.text, keywords))
    return scores


def kept_duration_in_window(
    start: float, end: float, speech: list[Segment]
) -> float:
    """[start, end] のうち発話区間に含まれる長さ (=カット後の実尺)。"""
    total = 0.0
    for seg in speech:
        a = max(start, seg.start)
        b = min(end, seg.end)
        if b > a:
            total += b - a
    return total


def find_highlights(
    transcript: list[TranscriptSegment],
    speech: list[Segment],
    max_duration: float = 60.0,
    count: int = 3,
    min_duration: float = 10.0,
    keywords: list[str] | None = None,
    hook_weight: float = 2.0,
) -> list[Highlight]:
    """スコア上位の重ならないハイライト候補を返す (ソース時刻順)。

    hook_weight は窓の先頭セグメントへの加重。ショートは冒頭の掴みで
    離脱が決まるため、面白い発言から始まる窓を優先する。
    """
    if not transcript:
        return []
    scores = _segment_scores(transcript, keywords)

    # 各開始位置から、カット後実尺が max_duration に収まる最長の窓を作る
    candidates: list[Highlight] = []
    n = len(transcript)
    for i in range(n):
        acc_score = hook_weight * scores[i]  # 冒頭フックの加重
        best: Highlight | None = None
        for j in range(i, n):
            kept = kept_duration_in_window(
                transcript[i].start, transcript[j].end, speech
            )
            if kept > max_duration:
                break
            acc_score += scores[j]
            if kept >= min_duration:
                best = Highlight(
                    start=transcript[i].start,
                    end=transcript[j].end,
                    score=acc_score,
                    kept_duration=kept,
                )
        if best is not None:
            best.preview = _preview_text(transcript, best.start, best.end)
            candidates.append(best)

    # スコア順に、重ならないものを count 個まで採用
    chosen: list[Highlight] = []
    for cand in sorted(candidates, key=lambda h: h.score, reverse=True):
        if len(chosen) >= count:
            break
        if all(cand.end <= c.start or cand.start >= c.end for c in chosen):
            chosen.append(cand)
    return sorted(chosen, key=lambda h: h.start)


def _preview_text(
    transcript: list[TranscriptSegment], start: float, end: float, limit: int = 40
) -> str:
    text = "".join(
        seg.text for seg in transcript if seg.start >= start and seg.end <= end
    )
    return text[:limit] + ("…" if len(text) > limit else "")
