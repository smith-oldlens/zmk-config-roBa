"""Image download with sidecar metadata."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import requests

from .models import Pin

log = logging.getLogger(__name__)

_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp)(?:\?|$)", re.IGNORECASE)


def download_pin(pin: Pin, download_dir: str | Path) -> Path | None:
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
    }
    dest.with_suffix(dest.suffix + ".json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dest
