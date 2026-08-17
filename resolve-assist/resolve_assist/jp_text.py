"""日本語テキストの整形ユーティリティ。

字幕の改行位置を決めるのに BudouX (Google, Apache-2.0) を使い、
文節(意味のまとまり)を崩さない自然な折り返しを行う。BudouX が
インストールされていない環境では句読点・助詞ベースの簡易ヒューリスティック
にフォールバックする。
"""

from __future__ import annotations

from functools import lru_cache

# 折り返し候補となる文字(この直後で改行する)— フォールバック用
_BREAK_AFTER = "、。!?!?…とがはをにでへもね"


@lru_cache(maxsize=1)
def _budoux_parser():
    """BudouX パーサを1度だけ生成してキャッシュする。未導入なら None。"""
    try:
        from budoux import load_default_japanese_parser

        return load_default_japanese_parser()
    except ImportError:
        return None


def budoux_available() -> bool:
    return _budoux_parser() is not None


def phrases(text: str) -> list[str]:
    """テキストを文節に分割する。BudouX があればそれを、無ければ全体を1要素で返す。"""
    parser = _budoux_parser()
    if parser is None:
        return [text]
    return parser.parse(text)


def _wrap_by_chars(text: str, max_chars: int) -> list[str]:
    """句読点・助詞ベースの簡易折り返し(BudouX 不在時のフォールバック)。"""
    text = text.strip()
    lines: list[str] = []
    while len(text) > max_chars:
        window = text[: max_chars + 1]
        break_at = -1
        for i in range(len(window) - 1, max(1, max_chars // 3), -1):
            if window[i - 1] in _BREAK_AFTER:
                break_at = i
                break
        if break_at <= 0:
            break_at = max_chars
        lines.append(text[:break_at].strip())
        text = text[break_at:].strip()
    if text:
        lines.append(text)
    return lines


def wrap_japanese(text: str, max_chars: int, use_budoux: bool = True) -> list[str]:
    """日本語テキストを最大 max_chars 文字で行に折り返す。

    use_budoux=True かつ BudouX 導入済みなら文節境界で折り返し、
    文節を分断しないようにする。1文節が max_chars を超える場合のみ
    その文節を文字ベースで分割する。
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    if not (use_budoux and budoux_available()):
        return _wrap_by_chars(text, max_chars)

    lines: list[str] = []
    current = ""
    for phrase in phrases(text):
        # 単一文節が長すぎる場合は文字ベースで割る
        if len(phrase) > max_chars:
            if current:
                lines.append(current)
                current = ""
            pieces = _wrap_by_chars(phrase, max_chars)
            lines.extend(pieces[:-1])
            current = pieces[-1] if pieces else ""
            continue
        if current and len(current) + len(phrase) > max_chars:
            lines.append(current)
            current = phrase
        else:
            current += phrase
    if current:
        lines.append(current)
    return lines
