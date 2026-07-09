"""Data model shared by all pin sources."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Pin:
    """A single Pinterest pin (or RSS item) candidate."""

    id: str
    title: str = ""
    description: str = ""
    image_url: str = ""
    link: str = ""
    source: str = ""  # "api" | "rss"
    score: float = 0.0
    score_details: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Concatenated text used for keyword scoring."""
        return f"{self.title}\n{self.description}".lower()
