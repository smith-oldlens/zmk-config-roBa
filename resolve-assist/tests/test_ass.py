from resolve_assist.export.ass import _ass_timestamp, format_ass
from resolve_assist.types import TranscriptSegment


def test_ass_timestamp():
    assert _ass_timestamp(0) == "0:00:00.00"
    assert _ass_timestamp(61.5) == "0:01:01.50"
    assert _ass_timestamp(-1) == "0:00:00.00"


def test_format_ass_structure():
    events = [
        TranscriptSegment(start=0.0, end=2.0, text="こんにちは"),
        TranscriptSegment(start=2.0, end=4.0, text="キーボードのレビューです"),
    ]
    ass = format_ass(events, font="TestFont", font_size=64, max_chars=8)
    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass
    assert "Style: Short,TestFont,64," in ass
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogues) == 2
    assert "0:00:00.00,0:00:02.00" in dialogues[0]
    assert "こんにちは" in dialogues[0]
    # 8文字超は \N で改行される
    assert "\\N" in dialogues[1]


def test_format_ass_skips_empty_and_invalid():
    events = [
        TranscriptSegment(start=0.0, end=1.0, text="  "),
        TranscriptSegment(start=2.0, end=1.0, text="逆転"),
    ]
    ass = format_ass(events)
    assert not [l for l in ass.splitlines() if l.startswith("Dialogue:")]


def test_format_ass_escapes_braces():
    events = [TranscriptSegment(start=0, end=1, text="{override}攻撃")]
    ass = format_ass(events)
    assert "{override}" not in ass
    assert "(override)" in ass
