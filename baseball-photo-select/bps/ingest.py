"""Ingest: verify, rename, register (spec 02 §4).

The rules that matter here, from docs/01 "変更禁止事項" (invariants):
  * photo data is never deleted — failures move to quarantine/, they don't unlink;
  * a source directory outside base_dir (a memory card) is only ever *read*,
    files are copied out of it, never moved;
  * nothing is registered until the file has been verified complete, because a
    half-written FTP transfer looks exactly like a corrupt photo to the scorer.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import piexif

from . import db as dbmod
from .config import Config
from .log import get_logger

log = get_logger("bps.ingest")

JPEG_EXTS = {".jpg", ".jpeg"}
RAW_EXTS = {".arw"}
EOI_SEARCH_BYTES = 64 * 1024  # spec §4.1: look for FFD9 in the last 64KB
_FILE_NUMBER_RE = re.compile(r"(\d{4,5})")


@dataclass
class IngestResult:
    """Outcome of one ingest run (also used for `bps ingest` reporting)."""

    registered: int = 0
    skipped_duplicate: int = 0
    quarantined: int = 0
    raw_moved: int = 0
    ignored: int = 0

    def __str__(self) -> str:
        return (
            f"registered={self.registered} duplicate={self.skipped_duplicate} "
            f"quarantined={self.quarantined} raw={self.raw_moved} ignored={self.ignored}"
        )


# --- 4.1 completeness verification --------------------------------------


def has_jpeg_eoi(path: Path) -> bool:
    """True if the JPEG end-of-image marker FFD9 is in the final 64KB."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > EOI_SEARCH_BYTES:
                fh.seek(-EOI_SEARCH_BYTES, os.SEEK_END)
            tail = fh.read()
    except OSError:
        return False
    return b"\xff\xd9" in tail


def can_open_exclusively(path: Path, attempts: int = 5, delay: float = 1.0) -> bool:
    """Best-effort check that no other process is still writing the file.

    Windows uses msvcrt.locking (spec §4.1); POSIX only has advisory locks, so
    there the size-stability and EOI checks carry the weight.
    """
    for attempt in range(attempts):
        try:
            fd = os.open(str(path), os.O_RDONLY)
        except OSError:
            time.sleep(delay if attempt < attempts - 1 else 0)
            continue
        try:
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    time.sleep(delay if attempt < attempts - 1 else 0)
                    continue
            return True
        finally:
            os.close(fd)
    return False


def size_is_stable(path: Path, interval: float) -> bool:
    """Size unchanged across `interval` seconds (spec §4.1 step 1)."""
    try:
        first = path.stat().st_size
    except OSError:
        return False
    if interval > 0:
        time.sleep(interval)
    try:
        second = path.stat().st_size
    except OSError:
        return False
    return first == second and second > 0


def verify_file(path: Path, cfg: Config, *, wait_stable: bool = True) -> bool:
    """Run the spec §4.1 completeness checks on one JPEG.

    `wait_stable=False` skips the size-stability sleep for batch ingest of a
    card directory, where files are already at rest — waiting 2s per file would
    make a 1000 shot card take over half an hour before any scoring starts.
    Watch mode (M4), where files arrive over FTP, keeps the wait.
    """
    interval = cfg.ingest.size_stable_seconds if wait_stable else 0.0
    if not size_is_stable(path, interval):
        return False
    if not can_open_exclusively(path):
        return False
    if not has_jpeg_eoi(path):
        return False
    return read_shot_time(path) is not None


# --- EXIF ---------------------------------------------------------------


def read_shot_time(path: Path) -> datetime | None:
    """EXIF DateTimeOriginal + SubSecTimeOriginal, or None if unreadable."""
    try:
        exif = piexif.load(str(path))
    except Exception:  # piexif raises bare Exception for malformed files
        return None
    ifd = exif.get("Exif") or {}
    raw = ifd.get(piexif.ExifIFD.DateTimeOriginal)
    if raw is None:
        zeroth = exif.get("0th") or {}
        raw = zeroth.get(piexif.ImageIFD.DateTime)
    if raw is None:
        return None
    text = raw.decode("ascii", "ignore").strip() if isinstance(raw, bytes) else str(raw).strip()
    try:
        stamp = datetime.strptime(text, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None

    subsec_raw = ifd.get(piexif.ExifIFD.SubSecTimeOriginal)
    micro = 0
    if subsec_raw is not None:
        digits = "".join(
            ch
            for ch in (
                subsec_raw.decode("ascii", "ignore")
                if isinstance(subsec_raw, bytes)
                else str(subsec_raw)
            )
            if ch.isdigit()
        )
        if digits:
            micro = int(f"{digits[:3]:0<3}") * 1000  # milliseconds -> microseconds
    return stamp.replace(microsecond=micro)


def format_shot_time(stamp: datetime) -> str:
    """Schema format: 'YYYY-MM-DD HH:MM:SS.fff' (spec §3)."""
    return stamp.strftime("%Y-%m-%d %H:%M:%S.") + f"{stamp.microsecond // 1000:03d}"


# --- 4.3 / 4.4 naming ----------------------------------------------------


def build_new_name(stamp: datetime, orig_name: str) -> str:
    """`{%Y%m%d}_{%H%M%S}_{subsec:03d}_{orig_stem}.jpg` (spec §4.3)."""
    stem = Path(orig_name).stem
    return (
        f"{stamp.strftime('%Y%m%d_%H%M%S')}_"
        f"{stamp.microsecond // 1000:03d}_{stem}.jpg"
    )


def extract_file_number(orig_name: str) -> int:
    """Last 4-5 digit run in the filename, or -1 when absent (spec §4.4)."""
    matches = _FILE_NUMBER_RE.findall(Path(orig_name).stem)
    return int(matches[-1]) if matches else -1


def arw_name_for(orig_name: str) -> str:
    """Companion RAW filename used for sidecar output (spec §7.4)."""
    return Path(orig_name).stem + ".ARW"


def _resolve_collision(
    database: dbmod.Database, work_dir: Path, new_name: str, source: Path
) -> tuple[str, bool]:
    """Return (name_to_use, is_duplicate).

    A re-run over the same card produces byte-identical files, which must be
    skipped rather than registered twice (M1 idempotency). A genuinely
    different photo that lands on the same name — same DSC number after a
    rollover, same timestamp — gets a `_dupN` suffix instead (spec §4.3).
    """
    existing = database.photo_by_new_name(new_name)
    if existing is None:
        return new_name, False

    stored = work_dir / new_name
    try:
        same_bytes = stored.is_file() and stored.stat().st_size == source.stat().st_size
    except OSError:
        same_bytes = False
    if same_bytes:
        return new_name, True

    stem, suffix = new_name[: -len(".jpg")], ".jpg"
    for n in range(1, 1000):
        candidate = f"{stem}_dup{n}{suffix}"
        if database.photo_by_new_name(candidate) is None and not (work_dir / candidate).exists():
            log.warning(
                "name collision for %s; registering as %s (different file, same timestamp)",
                new_name,
                candidate,
            )
            return candidate, False
    raise RuntimeError(f"could not resolve name collision for {new_name}")


# --- moving files --------------------------------------------------------


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _transfer(source: Path, dest: Path, *, move: bool) -> None:
    """Move within our own tree, copy when the source is external (a card)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(source), str(dest))
    else:
        shutil.copy2(str(source), str(dest))


def _already_quarantined(database: dbmod.Database, cfg: Config, source: Path) -> bool:
    """True if this exact file was quarantined by an earlier run.

    Card sources are copied, not moved, so a corrupt frame is still on the card
    the next time ingest runs; without this check every re-run would stack up
    another quarantine copy and another DB row.
    """
    try:
        size = source.stat().st_size
    except OSError:
        return False
    for row in database.photos_by_orig_name(source.name, dbmod.QUARANTINED):
        stored = cfg.quarantine_dir / Path(row["new_name"]).name
        try:
            if stored.is_file() and stored.stat().st_size == size:
                return True
        except OSError:
            continue
    return False


def _quarantine(source: Path, cfg: Config, *, move: bool) -> Path:
    """Park an unverifiable file for inspection. Never deletes (docs/01 inv. 3)."""
    dest = cfg.quarantine_dir / source.name
    counter = 1
    while dest.exists():
        dest = cfg.quarantine_dir / f"{source.stem}_{counter}{source.suffix}"
        counter += 1
    _transfer(source, dest, move=move)
    return dest


# --- the ingest entry points --------------------------------------------


def ingest_file(
    path: Path,
    cfg: Config,
    database: dbmod.Database,
    *,
    wait_stable: bool = True,
    move: bool = True,
    result: IngestResult | None = None,
) -> int | None:
    """Verify, rename and register one JPEG. Returns the photo id, or None.

    None means the file was skipped: already ingested, quarantined, or not a
    JPEG. The photo lands in VERIFIED — grouping is M2's job.
    """
    result = result if result is not None else IngestResult()
    suffix = path.suffix.lower()

    if suffix in RAW_EXTS:
        # RAWs are kept aside for the later selective copy; they are not scored.
        dest = cfg.arw_dir / path.name
        if dest.exists():
            log.debug("RAW already staged, skipping: %s", path.name)
        else:
            _transfer(path, dest, move=move)
            result.raw_moved += 1
        return None
    if suffix not in JPEG_EXTS:
        log.debug("ignoring non-photo file: %s", path.name)
        result.ignored += 1
        return None

    received_at = time.time()
    attempts = cfg.ingest.max_verify_retries
    for attempt in range(1, attempts + 1):
        if verify_file(path, cfg, wait_stable=wait_stable):
            break
        log.debug("verification failed for %s (attempt %d/%d)", path.name, attempt, attempts)
    else:
        if _already_quarantined(database, cfg, path):
            log.debug("already quarantined, skipping: %s", path.name)
            result.skipped_duplicate += 1
            return None
        dest = _quarantine(path, cfg, move=move)
        log.warning("quarantined %s after %d failed verifications -> %s", path.name, attempts, dest)
        _record_quarantine(database, path, dest, received_at)
        result.quarantined += 1
        return None

    stamp = read_shot_time(path)
    if stamp is None:  # verify_file already proved this parses; guard anyway
        dest = _quarantine(path, cfg, move=move)
        _record_quarantine(database, path, dest, received_at)
        result.quarantined += 1
        return None

    new_name = build_new_name(stamp, path.name)
    new_name, is_duplicate = _resolve_collision(database, cfg.work_dir, new_name, path)
    if is_duplicate:
        log.debug("already ingested, skipping: %s", path.name)
        result.skipped_duplicate += 1
        return None

    photo_id = database.insert_photo(
        orig_name=path.name,
        new_name=new_name,
        file_number=extract_file_number(path.name),
        shot_time=format_shot_time(stamp),
        received_at=received_at,
        state=dbmod.RECEIVED,
        arw_name=arw_name_for(path.name),
    )
    _transfer(path, cfg.work_dir / new_name, move=move)
    database.transition(photo_id, dbmod.RECEIVED, dbmod.VERIFIED)
    result.registered += 1
    return photo_id


def _record_quarantine(
    database: dbmod.Database, source: Path, dest: Path, received_at: float
) -> None:
    """Keep an audit row for a quarantined file (no EXIF, so shot_time is empty)."""
    try:
        database.insert_photo(
            orig_name=source.name,
            new_name=f"quarantine/{dest.name}",
            file_number=extract_file_number(source.name),
            shot_time="",
            received_at=received_at,
            state=dbmod.QUARANTINED,
        )
    except sqlite3.IntegrityError:
        log.debug("quarantine row already exists for %s", dest.name)


def ingest_dir(
    source_dir: Path,
    cfg: Config,
    database: dbmod.Database,
    *,
    wait_stable: bool | None = None,
    progress: bool = False,
) -> IngestResult:
    """Ingest every photo in a directory (spec §8 `bps ingest <dir>`).

    Files inside base_dir (i.e. inbox/) are moved; anything else — a memory
    card — is copied so the original is left untouched.
    """
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise NotADirectoryError(f"not a directory: {source_dir}")
    cfg.ensure_dirs()

    move = _is_inside(source_dir, cfg.base_dir)
    # Batch sources are at rest; only live FTP arrivals need the stability wait.
    if wait_stable is None:
        wait_stable = move

    entries = sorted(p for p in source_dir.iterdir() if p.is_file())
    result = IngestResult()
    total = len(entries)
    for index, entry in enumerate(entries, start=1):
        try:
            ingest_file(
                entry, cfg, database, wait_stable=wait_stable, move=move, result=result
            )
        except Exception as exc:  # one bad file must not stop the run
            log.exception("ingest failed for %s: %s", entry.name, exc)
        if progress and (index % 25 == 0 or index == total):
            print(f"  {index}/{total} files processed ({result})", flush=True)
    log.info("ingest of %s complete: %s", source_dir, result)
    return result
