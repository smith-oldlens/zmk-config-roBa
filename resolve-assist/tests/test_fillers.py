from resolve_assist.analysis.fillers import (
    detect_fillers,
    filler_cut_segments,
    load_filler_dict,
    normalize_token,
)
from resolve_assist.types import Word


def w(start, end, text):
    return Word(start=start, end=end, text=text)


def test_normalize_strips_punctuation_and_space():
    assert normalize_token(" えーっと、") == "えーっと"
    assert normalize_token("あの〜") == "あのー"


def test_detect_basic_fillers():
    words = [
        w(0.0, 0.4, "えーと"),
        w(0.5, 0.9, "今日は"),
        w(1.0, 1.3, "あのー"),
        w(1.4, 2.0, "キーボードの"),
        w(2.1, 2.4, "なんか、"),
        w(2.5, 3.0, "話です"),
    ]
    hits = detect_fillers(words)
    assert [h.text for h in hits] == ["えーと", "あのー", "なんか、"]


def test_no_partial_match():
    # 「あの人」のような複合語は単語トークンが分かれない限りヒットしない
    words = [w(0, 0.5, "あの人"), w(0.6, 1.0, "ですね、まあまあ")]
    hits = detect_fillers(words)
    assert hits == []


def test_filler_cut_segments_padding():
    hits = detect_fillers([w(1.0, 1.5, "えー")])
    segs = filler_cut_segments(hits, pad=0.05)
    assert len(segs) == 1
    assert round(segs[0].start, 3) == 0.95
    assert round(segs[0].end, 3) == 1.55


def test_load_filler_dict_text(tmp_path):
    f = tmp_path / "fillers.txt"
    f.write_text("# コメント\nえー\nなんか\n\n", encoding="utf-8")
    assert load_filler_dict(f) == ["えー", "なんか"]


def test_load_filler_dict_json(tmp_path):
    f = tmp_path / "fillers.json"
    f.write_text('["えー", "あの"]', encoding="utf-8")
    assert load_filler_dict(f) == ["えー", "あの"]
