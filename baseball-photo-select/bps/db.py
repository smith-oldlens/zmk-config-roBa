"""SQLite state store (spec 02 §2 state machine, §3 schema).

All state lives here and nowhere else: the pipeline must be able to crash at
any point and resume from the DB plus a rescan of work/ (docs/01 invariant 4).
Every state change goes through `transition()`, which refuses to move a photo
whose current state is not the expected one — that is what makes double
processing impossible when watchdog and the rescan loop both spot a file.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "1"

# --- states (spec 02 §2) ------------------------------------------------
RECEIVED = "RECEIVED"
VERIFIED = "VERIFIED"
GROUPED = "GROUPED"
SCORED = "SCORED"
WRITTEN = "WRITTEN"
DELIVERED = "DELIVERED"
QUARANTINED = "QUARANTINED"
FAILED = "FAILED"

ALL_STATES = (
    RECEIVED,
    VERIFIED,
    GROUPED,
    SCORED,
    WRITTEN,
    DELIVERED,
    QUARANTINED,
    FAILED,
)
#: States that still need work; the recovery scan re-submits these (spec §2, §9.3).
RESUMABLE_STATES = (RECEIVED, VERIFIED, GROUPED, SCORED, FAILED)
#: Terminal states: nothing further happens to these photos.
TERMINAL_STATES = (DELIVERED, QUARANTINED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
  id            INTEGER PRIMARY KEY,
  orig_name     TEXT NOT NULL,
  new_name      TEXT UNIQUE NOT NULL,
  file_number   INTEGER NOT NULL,
  shot_time     TEXT NOT NULL,
  received_at   REAL NOT NULL,
  state         TEXT NOT NULL,
  group_id      INTEGER REFERENCES groups(id),
  af_json       TEXT,
  scores_json   TEXT,
  rating        INTEGER,
  label         TEXT,
  error         TEXT,
  updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_photos_state ON photos(state);
CREATE INDEX IF NOT EXISTS idx_photos_group ON photos(group_id);

CREATE TABLE IF NOT EXISTS groups (
  id            INTEGER PRIMARY KEY,
  start_shot    TEXT NOT NULL,
  end_shot      TEXT NOT NULL,
  last_received REAL NOT NULL,
  finalized_at  REAL,
  best_photo_id INTEGER
);

CREATE TABLE IF NOT EXISTS name_map (
  new_name  TEXT PRIMARY KEY,
  orig_name TEXT NOT NULL,
  arw_name  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class TransitionError(Exception):
    """A photo was not in the state the caller expected (double processing guard)."""


class Database:
    """Thin SQLite wrapper. Not thread-safe by itself; see spec §9.1 threading."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    # --- lifecycle -------------------------------------------------------
    def init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(SCHEMA)
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- meta ------------------------------------------------------------
    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def start_session(self, now: float | None = None) -> float:
        """Mark the start of a culling session (sharpness calibration window, spec §6.3)."""
        started = time.time() if now is None else now
        self.set_meta("session_started_at", repr(started))
        return started

    # --- photos ----------------------------------------------------------
    def insert_photo(
        self,
        *,
        orig_name: str,
        new_name: str,
        file_number: int,
        shot_time: str,
        received_at: float,
        state: str = RECEIVED,
        arw_name: str | None = None,
    ) -> int:
        """Insert a photo row and its name_map entry in one transaction (spec §4.3)."""
        if state not in ALL_STATES:
            raise ValueError(f"unknown state {state!r}")
        now = time.time()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO photos(orig_name, new_name, file_number, shot_time, "
                "received_at, state, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (orig_name, new_name, file_number, shot_time, received_at, state, now),
            )
            if arw_name is not None:
                self.conn.execute(
                    "INSERT OR REPLACE INTO name_map(new_name, orig_name, arw_name) VALUES (?, ?, ?)",
                    (new_name, orig_name, arw_name),
                )
        return int(cur.lastrowid)

    def transition(self, photo_id: int, from_state: str, to_state: str, **fields: Any) -> None:
        """Move a photo between states, refusing if it is not in `from_state`.

        Extra keyword arguments update columns in the same statement, e.g.
        ``transition(pid, VERIFIED, GROUPED, group_id=7)``. Dict/list values for
        the *_json columns are serialised automatically.
        """
        if to_state not in ALL_STATES:
            raise ValueError(f"unknown target state {to_state!r}")
        assignments = ["state = ?", "updated_at = ?"]
        params: list[Any] = [to_state, time.time()]
        for column, value in fields.items():
            if column not in _UPDATABLE_COLUMNS:
                raise ValueError(f"column {column!r} is not updatable via transition()")
            if column.endswith("_json") and not isinstance(value, (str, type(None))):
                value = json.dumps(value, ensure_ascii=False)
            assignments.append(f"{column} = ?")
            params.append(value)
        params.extend([photo_id, from_state])
        with self.conn:
            cur = self.conn.execute(
                f"UPDATE photos SET {', '.join(assignments)} WHERE id = ? AND state = ?",
                params,
            )
            if cur.rowcount == 0:
                row = self.conn.execute(
                    "SELECT state FROM photos WHERE id = ?", (photo_id,)
                ).fetchone()
                actual = row["state"] if row else "<missing>"
                raise TransitionError(
                    f"photo {photo_id}: expected state {from_state!r} but found {actual!r} "
                    f"(target {to_state!r})"
                )

    def get_photo(self, photo_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()

    def photo_by_new_name(self, new_name: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM photos WHERE new_name = ?", (new_name,)
        ).fetchone()

    def photos_by_orig_name(self, orig_name: str, state: str | None = None) -> list[sqlite3.Row]:
        if state is None:
            return list(
                self.conn.execute("SELECT * FROM photos WHERE orig_name = ?", (orig_name,))
            )
        return list(
            self.conn.execute(
                "SELECT * FROM photos WHERE orig_name = ? AND state = ?", (orig_name, state)
            )
        )

    def photos_in_state(self, state: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM photos WHERE state = ? ORDER BY shot_time, id", (state,)
            )
        )

    def photos_in_states(self, states: Iterable[str]) -> list[sqlite3.Row]:
        states = list(states)
        placeholders = ", ".join("?" for _ in states)
        return list(
            self.conn.execute(
                f"SELECT * FROM photos WHERE state IN ({placeholders}) ORDER BY shot_time, id",
                states,
            )
        )

    def counts_by_state(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT state, COUNT(*) AS n FROM photos GROUP BY state")
        counts = {row["state"]: int(row["n"]) for row in rows}
        return {state: counts.get(state, 0) for state in ALL_STATES}

    def recent_errors(self, limit: int = 5) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT new_name, state, error, updated_at FROM photos "
                "WHERE error IS NOT NULL ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
        )

    def file_numbers(self) -> set[int]:
        """All known file numbers — used by the missing-number check (spec §5.2)."""
        return {
            int(row["file_number"])
            for row in self.conn.execute(
                "SELECT DISTINCT file_number FROM photos WHERE file_number >= 0"
            )
        }

    # --- name_map --------------------------------------------------------
    def arw_name_for(self, new_name: str) -> str | None:
        row = self.conn.execute(
            "SELECT arw_name FROM name_map WHERE new_name = ?", (new_name,)
        ).fetchone()
        return row["arw_name"] if row else None

    # --- groups ----------------------------------------------------------
    def create_group(self, start_shot: str, end_shot: str, last_received: float) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO groups(start_shot, end_shot, last_received) VALUES (?, ?, ?)",
                (start_shot, end_shot, last_received),
            )
        return int(cur.lastrowid)

    def photos_in_group(self, group_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM photos WHERE group_id = ? ORDER BY shot_time, id", (group_id,)
            )
        )

    def scores_since(self, session_started_at: float) -> list[str]:
        """scores_json of every photo scored in the current session (spec §6.3)."""
        return [
            row["scores_json"]
            for row in self.conn.execute(
                "SELECT scores_json FROM photos WHERE scores_json IS NOT NULL "
                "AND received_at >= ?",
                (session_started_at,),
            )
        ]

    def set_scores(self, photo_id: int, scores_json: str) -> None:
        """Store raw scores without changing state (two-pass calibration)."""
        with self.conn:
            self.conn.execute(
                "UPDATE photos SET scores_json = ?, updated_at = ? WHERE id = ?",
                (scores_json, time.time(), photo_id),
            )

    def close_group(self, group_id: int, best_photo_id: int | None, when: float | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE groups SET finalized_at = ?, best_photo_id = ? WHERE id = ?",
                (time.time() if when is None else when, best_photo_id, group_id),
            )

    def open_groups(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute("SELECT * FROM groups WHERE finalized_at IS NULL ORDER BY id")
        )

    def count_open_groups(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM groups WHERE finalized_at IS NULL"
        ).fetchone()
        return int(row["n"])


_UPDATABLE_COLUMNS = {
    "group_id",
    "af_json",
    "scores_json",
    "rating",
    "label",
    "error",
    "new_name",
}
