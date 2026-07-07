"""CLI entry point: python -m collector --config config.yaml"""
from __future__ import annotations

import argparse
import logging
import sys

from . import config as config_mod
from .downloader import download_pin
from .models import Pin
from .scoring import score_pins
from .sources import fetch_api_pins, fetch_rss_pins
from .sources.api import save_pin_to_board
from .state import State

log = logging.getLogger("collector")


def collect(cfg: dict, dry_run: bool = False) -> int:
    token = config_mod.api_token()
    sources_cfg = cfg["sources"]
    pins: list[Pin] = []

    if sources_cfg["api"]["enabled"]:
        if token:
            pins += fetch_api_pins(
                token,
                sources_cfg["api"].get("queries", []),
                per_query=int(sources_cfg["api"].get("per_query", 25)),
            )
        else:
            log.warning("API source is enabled but PINTEREST_ACCESS_TOKEN is not set; skipping.")

    if sources_cfg["rss"]["enabled"]:
        pins += fetch_rss_pins(sources_cfg["rss"].get("feeds", []))

    if not pins:
        log.info("No pins fetched. Enable at least one source in the config.")
        return 0

    # Deduplicate within this run, then against previous runs.
    unique: dict[str, Pin] = {}
    for pin in pins:
        unique.setdefault(pin.id, pin)
    state = State(cfg["state_file"])
    fresh = [p for p in unique.values() if not state.is_seen(p.id)]
    log.info("Fetched %d pins (%d new).", len(unique), len(fresh))

    prefs = cfg["preferences"]
    fresh = score_pins(fresh, prefs)
    min_score = float(prefs.get("min_score", 1.0))
    picked = sorted(
        (p for p in fresh if p.score >= min_score),
        key=lambda p: p.score,
        reverse=True,
    )[: int(cfg["output"].get("max_items_per_run", 30))]

    log.info("%d pins passed the preference filter (min_score=%s).", len(picked), min_score)

    board_id = cfg["output"].get("save_to_board")
    downloaded = 0
    for pin in picked:
        log.info("  [%.2f] %s %s %s", pin.score, pin.title[:60] or "(no title)", pin.link, pin.score_details)
        if dry_run:
            continue
        if download_pin(pin, cfg["output"]["download_dir"]):
            downloaded += 1
        if board_id and token and pin.source == "api":
            save_pin_to_board(token, pin.id, str(board_id))
        state.mark_seen(pin.id)

    if not dry_run:
        # Pins that were fetched but rejected also count as seen, so the next
        # run doesn't rescore the same rejects forever.
        for pin in fresh:
            state.mark_seen(pin.id)
        state.save()
        log.info("Downloaded %d images to %s", downloaded, cfg["output"]["download_dir"])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="collector",
        description="Collect Pinterest pins matching your taste, from the official API and public RSS feeds.",
    )
    parser.add_argument("--config", "-c", default="config.yaml", help="Path to config YAML")
    parser.add_argument("--dry-run", action="store_true", help="Score and list pins without downloading or saving")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    cfg = config_mod.load_config(args.config)
    return collect(cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
