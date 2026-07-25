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
from .ingest import ingest_dir
from .log import setup_logging

DEFAULT_CONFIG = "config.yaml"
_NOT_YET = {
    "watch": "M4",
    "finalize": "M2/M4",
    "export-raw": "M3",
    "train": "M5",
    "calibrate": "M2",
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
    with _open_db(cfg) as database:
        database.start_session()
        result = ingest_dir(source, cfg, database, progress=not args.quiet)
        counts = database.counts_by_state()
    print(f"\nIngest complete: {result}")
    print("State counts: " + ", ".join(f"{s}={n}" for s, n in counts.items() if n))
    if result.quarantined:
        print(f"  {result.quarantined} file(s) in {cfg.quarantine_dir} — inspect, nothing deleted.")
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

    for name, milestone in _NOT_YET.items():
        sub.add_parser(name, help=f"(not yet implemented — {milestone})")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {"init": cmd_init, "ingest": cmd_ingest, "status": cmd_status}
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
