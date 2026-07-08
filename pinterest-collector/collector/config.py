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
        "gallery": True,
        "gallery_file": "gallery.html",
    },
    "state_file": "./state.json",
    "token_cache_file": "./token_cache.json",
    "feedback_file": "./feedback.json",
    "learned_file": "./learned.json",
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
    cfg["token_cache_file"] = str((base / cfg["token_cache_file"]).resolve())
    cfg["feedback_file"] = str((base / cfg["feedback_file"]).resolve())
    cfg["learned_file"] = str((base / cfg["learned_file"]).resolve())

    # The gallery lives inside the download dir so it can reference images by
    # their bare filename.
    gallery_file = Path(cfg["output"]["gallery_file"])
    if not gallery_file.is_absolute():
        gallery_file = Path(cfg["output"]["download_dir"]) / gallery_file
    cfg["output"]["gallery_file"] = str(gallery_file)
    return cfg


def api_credentials() -> dict:
    """Pinterest API credentials from the environment.

    - PINTEREST_ACCESS_TOKEN alone works but expires (~30 days) with no
      automatic renewal.
    - Adding PINTEREST_CLIENT_ID/SECRET + PINTEREST_REFRESH_TOKEN (obtained
      once via `python -m collector --setup-auth`) enables auto-refresh, so
      the token never needs to be manually replaced again.
    """
    return {
        "client_id": os.environ.get("PINTEREST_CLIENT_ID"),
        "client_secret": os.environ.get("PINTEREST_CLIENT_SECRET"),
        "access_token": os.environ.get("PINTEREST_ACCESS_TOKEN"),
        "refresh_token": os.environ.get("PINTEREST_REFRESH_TOKEN"),
    }
