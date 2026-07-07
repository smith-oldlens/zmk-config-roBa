"""Seen-pin state, for deduplication across runs."""
from __future__ import annotations

import json
from pathlib import Path


class State:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.seen: set[str] = set()
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.seen = set(data.get("seen", []))
            except (json.JSONDecodeError, OSError):
                self.seen = set()

    def is_seen(self, pin_id: str) -> bool:
        return pin_id in self.seen

    def mark_seen(self, pin_id: str) -> None:
        self.seen.add(pin_id)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"seen": sorted(self.seen)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
