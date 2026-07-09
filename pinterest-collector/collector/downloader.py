"""Image download with sidecar metadata and perceptual dedupe."""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path

import requests

from .dedupe import dhash, find_duplicate
from .models import Pin
from .state import State

log = logging.getLogger(__name__)

_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp)(?:\?|$)", re.IGNORECASE)


def download_pin(
    pin: Pin,
    download_dir: str | Path,
    state: State | None = None,
    dedupe_cfg: dict | None = None,
) -> Path | None:
    """Fetch the pin's image and write it plus a metadata sidecar.

    Returns the image path, or None when the fetch failed or the image is a
    perceptual duplicate of one already collected.
    """
    if not pin.image_url:
        return None
    directory = Path(download_dir)
    directory.mkdir(parents=True, exist_ok=True)

    ext_match = _EXT_RE.search(pin.image_url)
    ext = ext_match.group(1).lower().replace("jpeg", "jpg") if ext_match else "jpg"
    dest = directory / f"{pin.id}.{ext}"

    try:
        resp = requests.get(pin.image_url, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Download failed for pin %s (%s): %s", pin.id, pin.image_url, exc)
        return None

    image_hash = None
    if state is not None and (dedupe_cfg or {}).get("enabled", True):
        image_hash = dhash(resp.content)
        if image_hash:
            duplicate_of = find_duplicate(
                image_hash, state.hashes, int((dedupe_cfg or {}).get("max_distance", 5))
            )
            if duplicate_of:
                log.info("Pin %s is a duplicate image of pin %s; skipping.", pin.id, duplicate_of)
                return None
            state.add_hash(pin.id, image_hash)

    dest.write_bytes(resp.content)
    meta = {
        "id": pin.id,
        "title": pin.title,
        "description": pin.description,
        "link": pin.link,
        "image_url": pin.image_url,
        "source": pin.source,
        "score": pin.score,
        "score_details": pin.score_details,
        "collected_at": dt.datetime.now().isoformat(timespec="seconds"),
        "image_hash": image_hash,
    }
    dest.with_suffix(dest.suffix + ".json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dest
