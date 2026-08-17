"""パイプライン全体で使う共通データ型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Segment:
    """秒単位の時間区間 [start, end)。"""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Word:
    """単語1つ分のタイムスタンプ付きテキスト。"""

    start: float
    end: float
    text: str


@dataclass
class TranscriptSegment:
    """文字起こしの1セグメント。"""

    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Marker:
    """タイムライン/クリップに打つマーカー。sec はソース基準の秒。"""

    sec: float
    name: str
    note: str = ""
    color: str = "Red"
    duration_sec: float = 0.0


@dataclass
class MediaInfo:
    """ffprobe から取得した素材情報。"""

    path: str
    duration: float
    fps: float
    width: int = 0
    height: int = 0
    has_audio: bool = True
