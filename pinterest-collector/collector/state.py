"""Persistent run state: seen pins, image hashes, and the notify queue."""
from __future__ import annotations

import json
from pathlib import Path


class State:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.seen: set[str] = set()
        self.hashes: dict[str, str] = {}       # pin id -> dHash hex
        self.pending_notify: list[str] = []    # pin ids queued for the next email digest
        self.last_notified: float = 0.0        # epoch seconds of the last digest
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
            # Backward compatible with the original {"seen": [...]} format.
            self.seen = set(data.get("seen", []))
            self.hashes = dict(data.get("hashes", {}))
            self.pending_notify = list(data.get("pending_notify", []))
            self.last_notified = float(data.get("last_notified", 0.0))

    def is_seen(self, pin_id: str) -> bool:
        return pin_id in self.seen

    def mark_seen(self, pin_id: str) -> None:
        self.seen.add(pin_id)

    def add_hash(self, pin_id: str, image_hash: str) -> None:
        self.hashes[pin_id] = image_hash

    def queue_notify(self, pin_id: str) -> None:
        if pin_id not in self.pending_notify:
            self.pending_notify.append(pin_id)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "seen": sorted(self.seen),
                    "hashes": self.hashes,
                    "pending_notify": self.pending_notify,
                    "last_notified": self.last_notified,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
