"""Writing ratings into files and handing them to Lightroom (spec 02 §7.3-§7.4).

Two invariants shape this module (docs/01):

  * a photo only reaches deliver/ once its rating is written and verified —
    Lightroom reads metadata exactly once, at import, so a file delivered
    early is a file whose stars never appear;
  * the RAWs stay on the card. Only the keepers get a sidecar and a place on
    the copy list, so the shoot's 40GB of ARW never has to be imported to find
    the dozen frames worth developing.
"""
from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import db as dbmod
from .config import Config
from .log import get_logger
from .metadata import MetadataTool

log = get_logger("bps.deliver")

SELECT_LIST = "select_list.txt"
KEEPER_RATING = 3  # ratings at or above this are worth pulling the RAW for
RAW_EXTS = (".ARW", ".arw")


@dataclass
class DeliverResult:
    written: int = 0
    delivered: int = 0
    sidecars: int = 0
    failed: int = 0

    def __str__(self) -> str:
        return (
            f"written={self.written} delivered={self.delivered} "
            f"sidecars={self.sidecars} failed={self.failed}"
        )


@dataclass
class ExportResult:
    copied: int = 0
    sidecars: int = 0
    missing: list[str] = None  # ARWs listed but not found on the card

    def __post_init__(self) -> None:
        if self.missing is None:
            self.missing = []


# --- select list ---------------------------------------------------------


def select_list_path(cfg: Config) -> Path:
    return cfg.raw_select_dir / SELECT_LIST


def read_select_list(cfg: Config) -> list[str]:
    path = select_list_path(cfg)
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def add_to_select_list(cfg: Config, arw_name: str) -> None:
    """Append a RAW to the copy list, keeping it deduplicated and sorted."""
    entries = set(read_select_list(cfg))
    entries.add(arw_name)
    path = select_list_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sorted(entries)) + "\n", encoding="utf-8")


def sidecar_path_for(cfg: Config, arw_name: str) -> Path:
    """Lightroom matches a sidecar by base name, so drop the RAW extension."""
    return cfg.raw_select_dir / (Path(arw_name).stem + ".xmp")


# --- the delivery run ----------------------------------------------------


def _same_volume(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_dev == b.stat().st_dev
    except OSError:
        return True


def deliver_photo(
    row: sqlite3.Row,
    cfg: Config,
    database: dbmod.Database,
    metadata: MetadataTool,
    result: DeliverResult,
) -> bool:
    """Write one photo's rating, then move it into the watched folder."""
    photo_id = int(row["id"])
    source = cfg.work_dir / row["new_name"]
    if not source.is_file():
        database.transition(
            photo_id, dbmod.SCORED, dbmod.FAILED, error=f"missing before delivery: {source.name}"
        )
        result.failed += 1
        return False

    rating = row["rating"] if row["rating"] is not None else 0
    label = row["label"]
    if not metadata.write_rating(source, int(rating), label):
        database.transition(photo_id, dbmod.SCORED, dbmod.FAILED, error="rating write failed")
        result.failed += 1
        return False
    database.transition(photo_id, dbmod.SCORED, dbmod.WRITTEN)
    result.written += 1

    # Keepers get their RAW queued before the JPEG moves, so an interruption
    # leaves a redundant list entry rather than a lost selection.
    if int(rating) >= KEEPER_RATING:
        arw_name = database.arw_name_for(row["new_name"])
        if arw_name:
            if metadata.write_sidecar(sidecar_path_for(cfg, arw_name), int(rating), label):
                result.sidecars += 1
            add_to_select_list(cfg, arw_name)

    destination = cfg.deliver_dir / row["new_name"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(source), str(destination))
    except OSError as exc:
        database.transition(photo_id, dbmod.WRITTEN, dbmod.FAILED, error=f"move failed: {exc}")
        result.failed += 1
        return False
    database.transition(photo_id, dbmod.WRITTEN, dbmod.DELIVERED)
    result.delivered += 1
    return True


def deliver_scored(
    cfg: Config, database: dbmod.Database, *, progress: bool = False
) -> DeliverResult:
    """Deliver every SCORED photo. Safe to re-run; it only picks up new work."""
    cfg.ensure_dirs()
    pending = database.photos_in_state(dbmod.SCORED)
    result = DeliverResult()
    if not pending:
        return result

    if not _same_volume(cfg.work_dir, cfg.deliver_dir):
        log.warning(
            "work/ and deliver/ are on different volumes; each delivery will copy "
            "the whole file instead of renaming it"
        )

    with MetadataTool(cfg) as metadata:
        if not metadata.available:
            log.error("exiftool unavailable — cannot write ratings, nothing delivered")
            return result
        total = len(pending)
        for index, row in enumerate(pending, start=1):
            deliver_photo(row, cfg, database, metadata, result)
            if progress and (index % 25 == 0 or index == total):
                print(f"  {index}/{total} delivered ({result})", flush=True)

    log.info("delivery complete: %s", result)
    return result


# --- pulling the selected RAWs off the card ------------------------------


def _index_card(card_dir: Path) -> dict[str, Path]:
    """Map RAW file names on the card to their paths, searching subfolders."""
    index: dict[str, Path] = {}
    for ext in RAW_EXTS:
        for path in card_dir.rglob(f"*{ext}"):
            index.setdefault(path.name.upper(), path)
    return index


def export_raws(cfg: Config, card_dir: Path, dest_dir: Path) -> ExportResult:
    """Copy the selected ARWs plus their sidecars off the card (spec §7.4).

    Copies rather than moves: the card stays the original until the user
    decides otherwise (docs/01 invariant 3).
    """
    result = ExportResult()
    wanted = read_select_list(cfg)
    if not wanted:
        log.warning("select list is empty — nothing to export")
        return result

    dest_dir.mkdir(parents=True, exist_ok=True)
    available = _index_card(card_dir)
    for arw_name in wanted:
        source = available.get(arw_name.upper())
        if source is None:
            result.missing.append(arw_name)
            continue
        target = dest_dir / source.name
        if not target.exists():
            shutil.copy2(str(source), str(target))
        result.copied += 1

        sidecar = sidecar_path_for(cfg, arw_name)
        if sidecar.is_file():
            # Name the sidecar after the RAW as it actually landed, so Lightroom
            # pairs them even if the card used different capitalisation.
            shutil.copy2(str(sidecar), str(dest_dir / (Path(source.name).stem + ".xmp")))
            result.sidecars += 1

    if result.missing:
        log.warning(
            "%d selected RAW(s) not found on the card, e.g. %s",
            len(result.missing),
            ", ".join(result.missing[:5]),
        )
    log.info("exported %d RAW(s) with %d sidecar(s) to %s", result.copied, result.sidecars, dest_dir)
    return result
