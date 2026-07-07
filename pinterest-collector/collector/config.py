"""Configuration loading and validation."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

DEFAULTS = {
    "sources": {
        "api": {"enabled": False, "queries": [], "per_query": 25},
        "rss": {"enabled": False, "feeds": []},
    },
    "preferences": {
        "like_keywords": [],
        "dislike_keywords": [],
        "min_score": 1.0,
        "clip": {"enabled": False, "positive_prompts": [], "negative_prompts": [], "weight": 3.0},
    },
    "output": {
        "download_dir": "./collected",
        "save_to_board": None,
        "max_items_per_run": 30,
    },
    "state_file": "./state.json",
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path) -> dict:
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    cfg = _merge(DEFAULTS, raw)

    # Paths in the config are resolved relative to the config file itself,
    # so the tool behaves the same regardless of the current directory.
    base = path.parent
    cfg["output"]["download_dir"] = str((base / cfg["output"]["download_dir"]).resolve())
    cfg["state_file"] = str((base / cfg["state_file"]).resolve())
    return cfg


def api_token() -> str | None:
    return os.environ.get("PINTEREST_ACCESS_TOKEN")
