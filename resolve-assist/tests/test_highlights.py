from resolve_assist.analysis.highlights import (
    find_highlights,
    kept_duration_in_window,
    score_text,
)
from resolve_assist.types import Segment, TranscriptSegment


def ts(start, end, text):
    return TranscriptSegment(start=start, end=end, text=text)


def test_score_text_keywords_and_emphasis():
    assert score_text("普通の文章です") == 0.0
    assert score_text("これはおすすめです") >= 2.0
    assert score_text("すごい!最強!") > score_text("すごい")


def test_kept_duration_in_window():
    speech = [Segment(0, 5), Segment(10, 15)]
    assert kept_duration_in_window(0, 20, speech) == 10.0
    assert kept_duration_in_window(3, 12, speech) == 4.0
    assert kept_duration_in_window(6, 9, speech) == 0.0


def test_find_highlights_picks_keyword_rich_window():
    # 前半は平凡、後半にキーワードが密集
    transcript = [
        ts(0, 5, "こんにちは"),
        ts(5, 10, "今日の天気の話です"),
        ts(20, 25, "ここが一番のポイントです"),
        ts(25, 30, "実はおすすめの設定があります"),
        ts(30, 35, "結論としては最強です"),
    ]
    speech = [Segment(0, 10), Segment(20, 35)]
    hits = find_highlights(
        transcript, speech, max_duration=20.0, count=1, min_duration=5.0
    )
    assert len(hits) == 1
    # キーワード密集地帯 (20-35s) が選ばれる
    assert hits[0].start >= 20.0
    assert hits[0].kept_duration <= 20.0
    assert hits[0].preview  # プレビューが付く


def test_find_highlights_respects_max_duration():
    transcript = [ts(i * 10, i * 10 + 8, f"セグメント{i}です") for i in range(10)]
    speech = [Segment(i * 10, i * 10 + 8) for i in range(10)]
    hits = find_highlights(
        transcript, speech, max_duration=30.0, count=3, min_duration=5.0
    )
    assert 1 <= len(hits) <= 3
    for h in hits:
        assert h.kept_duration <= 30.0
    # 候補同士は重ならない
    for a, b in zip(hits, hits[1:]):
        assert a.end <= b.start


def test_find_highlights_empty_transcript():
    assert find_highlights([], [Segment(0, 10)], 60, 3) == []
