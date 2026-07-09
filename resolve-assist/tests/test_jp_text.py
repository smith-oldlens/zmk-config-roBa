import pytest

from resolve_assist import jp_text
from resolve_assist.jp_text import _wrap_by_chars, budoux_available, wrap_japanese

budoux = pytest.importorskip("budoux") if False else None  # 実行はしない


def test_short_text_unchanged():
    assert wrap_japanese("こんにちは", 26) == ["こんにちは"]


def test_empty_text():
    assert wrap_japanese("   ", 10) == []


def test_fallback_wrap_respects_max_chars():
    text = "今日はキーボードの話をします、それでは始めましょう"
    lines = _wrap_by_chars(text, 15)
    assert all(len(line) <= 15 for line in lines)
    assert "".join(lines) == text.replace(" ", "")


def test_wrap_use_budoux_false_matches_fallback():
    text = "今日はキーボードの話をします、それでは始めましょう"
    assert wrap_japanese(text, 15, use_budoux=False) == _wrap_by_chars(text, 15)


def test_long_single_phrase_is_char_split():
    # BudouX があっても無くても、1文節が長すぎれば文字分割される
    text = "あ" * 50
    lines = wrap_japanese(text, 20)
    assert all(len(line) <= 20 for line in lines)
    assert "".join(lines) == text


@pytest.mark.skipif(not budoux_available(), reason="budoux 未インストール")
def test_budoux_keeps_phrases_together():
    # BudouX 導入時: 文節境界で折り返し、各行が上限内に収まる
    text = "今日は自作キーボードの魅力について詳しく解説していきます"
    lines = wrap_japanese(text, 12, use_budoux=True)
    assert len(lines) >= 2
    assert all(len(line) <= 12 for line in lines)
    # 元テキストが保持される (改行位置以外は変わらない)
    assert "".join(lines) == text


@pytest.mark.skipif(not budoux_available(), reason="budoux 未インストール")
def test_phrases_returns_multiple():
    result = jp_text.phrases("今日は良い天気ですね")
    assert len(result) >= 2
    assert "".join(result) == "今日は良い天気ですね"
