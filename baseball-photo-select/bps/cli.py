"""Command line interface (spec 02 §8).

Implemented in M1: init, ingest, status. The remaining subcommands are
declared but refuse to run until their milestone lands, so `bps --help`
always shows the real shape of the tool.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import db as dbmod
from .config import Config, ConfigError, load_config
from .grouping import group_pending
from .ingest import JPEG_EXTS, ingest_dir
from .log import setup_logging
from .scoring import sharpness as sharpmod
from .scoring.composite import finalize_ready_groups, load_image

DEFAULT_CONFIG = "config.yaml"
_NOT_YET = {
    "watch": "M4",
    "export-raw": "M3",
    "train": "M5",
}


def _load(args: argparse.Namespace) -> Config:
    cfg = load_config(args.config)
    setup_logging(cfg.logs_dir, cfg.logging.level)
    return cfg


def _open_db(cfg: Config) -> dbmod.Database:
    database = dbmod.Database(cfg.db_path)
    database.init_schema()
    return database


def cmd_init(args: argparse.Namespace) -> int:
    cfg = _load(args)
    cfg.ensure_dirs()
    with _open_db(cfg) as database:
        database.start_session()
    print(f"Initialised {cfg.base_dir}")
    for directory in cfg.all_dirs():
        print(f"  {directory}")
    print(f"  {cfg.db_path} (schema v{dbmod.SCHEMA_VERSION})")
    print(
        "\nNext: point Lightroom's auto-import at "
        f"{cfg.deliver_dir} (docs/04 §5), then run `bps ingest <card dir>`."
    )
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    cfg = _load(args)
    source = Path(args.source)
    if not source.is_dir():
        print(f"error: not a directory: {source}", file=sys.stderr)
        return 2
    verbose = not args.quiet
    with _open_db(cfg) as database:
        database.start_session()
        result = ingest_dir(source, cfg, database, progress=verbose)
        grouped = group_pending(cfg, database)
        # A batch source is complete by definition, so there is nothing left to
        # wait for: every group is finalised immediately (spec §5.2).
        summary = finalize_ready_groups(cfg, database, force=True, progress=verbose)
        counts = database.counts_by_state()

    print(f"\nIngest complete: {result}")
    print(f"Grouped {grouped} photo(s) into {summary['groups']} burst(s); rated {summary['rated']}.")
    print("State counts: " + ", ".join(f"{s}={n}" for s, n in counts.items() if n))
    if result.quarantined:
        print(f"  {result.quarantined} file(s) in {cfg.quarantine_dir} — inspect, nothing deleted.")
    if summary["missing_files"]:
        print(f"  {summary['missing_files']} photo(s) could not be read; see `bps status`.")
    print("\nRatings are in the database. Writing them into the files and delivering")
    print("to Lightroom is M3 (`bps export-raw` / deliver) — not implemented yet.")
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    cfg = _load(args)
    with _open_db(cfg) as database:
        grouped = group_pending(cfg, database)
        summary = finalize_ready_groups(cfg, database, force=args.all, progress=not args.quiet)
    if not summary["groups"]:
        print("Nothing ready to finalise." if not args.all else "No open groups.")
        return 0
    print(
        f"Grouped {grouped} new photo(s); finalised {summary['groups']} burst(s), "
        f"rated {summary['rated']} photo(s)."
    )
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Report the sharpness distribution of a sample folder (Phase 1 tuning)."""
    cfg = _load(args)
    sample = Path(args.sample)
    if not sample.is_dir():
        print(f"error: not a directory: {sample}", file=sys.stderr)
        return 2

    files = sorted(p for p in sample.rglob("*") if p.suffix.lower() in JPEG_EXTS)
    if not files:
        print(f"No JPEGs found under {sample}")
        return 1

    values: list[float] = []
    for index, path in enumerate(files, start=1):
        image = load_image(path)
        if image is None:
            continue
        # Whole-frame measurement: without the detector this matches what the
        # pipeline itself currently does (centre crop) closely enough to tune.
        values.append(sharpmod.raw_sharpness(image))
        if not args.quiet and (index % 50 == 0 or index == len(files)):
            print(f"  {index}/{len(files)} measured", flush=True)

    if not values:
        print("No readable JPEGs.")
        return 1

    ordered = sorted(values)
    def at(pct: float) -> float:
        return ordered[min(len(ordered) - 1, int(pct * len(ordered)))]

    print(f"\nSharpness (log10 of Laplacian variance) over {len(values)} photo(s):")
    for pct in (0.05, 0.15, 0.25, 0.50, 0.75, 0.95):
        print(f"  p{int(pct * 100):02d}  {at(pct):.3f}")
    print(f"  min  {ordered[0]:.3f}   max  {ordered[-1]:.3f}")
    print(
        f"\nCurrent config rejects below the {cfg.sharpness.reject_pct:.0%} percentile "
        f"(raw {at(cfg.sharpness.reject_pct):.3f}) and keeps at or above "
        f"{cfg.sharpness.keeper_pct:.0%} (raw {at(cfg.sharpness.keeper_pct):.3f})."
    )
    print(
        "Compare those cut-offs against your own picks before trusting them; the "
        "bootstrap_log10 setting only matters for the first 50 frames of a session."
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _load(args)
    with _open_db(cfg) as database:
        counts = database.counts_by_state()
        open_groups = database.count_open_groups()
        errors = database.recent_errors()
    total = sum(counts.values())
    print(f"base_dir: {cfg.base_dir}")
    print(f"photos:   {total}")
    for state in dbmod.ALL_STATES:
        if counts[state]:
            print(f"  {state:12} {counts[state]}")
    print(f"open groups (not finalized): {open_groups}")
    if errors:
        print("recent errors:")
        for row in errors:
            print(f"  {row['new_name']} [{row['state']}]: {row['error']}")
    return 0


def cmd_unimplemented(args: argparse.Namespace) -> int:
    milestone = _NOT_YET[args.command]
    print(
        f"`bps {args.command}` is not implemented yet (planned for {milestone}; "
        "see docs/03-milestones.md).",
        file=sys.stderr,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bps", description=__doc__.splitlines()[0])
    parser.add_argument(
        "-c", "--config", default=DEFAULT_CONFIG, help=f"config file (default {DEFAULT_CONFIG})"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create base_dir layout and initialise the database")

    p_ingest = sub.add_parser("ingest", help="batch-ingest a directory (card or inbox)")
    p_ingest.add_argument("source", help="directory to ingest, e.g. E:/DCIM/100MSDCF")
    p_ingest.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")

    sub.add_parser("status", help="show state counts, open groups and recent errors")

    p_final = sub.add_parser("finalize", help="score and rate groups that are ready")
    p_final.add_argument(
        "--all", action="store_true", help="force every open group (post-game batch)"
    )
    p_final.add_argument("-q", "--quiet", action="store_true", help="suppress per-group output")

    p_cal = sub.add_parser("calibrate", help="report the sharpness distribution of a sample folder")
    p_cal.add_argument("--sample", required=True, help="folder of past photos to measure")
    p_cal.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")

    for name, milestone in _NOT_YET.items():
        sub.add_parser(name, help=f"(not yet implemented — {milestone})")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "init": cmd_init,
        "ingest": cmd_ingest,
        "status": cmd_status,
        "finalize": cmd_finalize,
        "calibrate": cmd_calibrate,
    }
    handler = handlers.get(args.command, cmd_unimplemented)
    try:
        return handler(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
