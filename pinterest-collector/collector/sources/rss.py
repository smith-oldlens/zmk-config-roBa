"""Public RSS feed source.

Pinterest exposes RSS feeds for public users and boards without any API key:

    https://www.pinterest.com/<username>/feed.rss
    https://www.pinterest.com/<username>/<board-slug>.rss

Following the feeds of artists/boards you already like is a lightweight,
ToS-friendly way to keep collecting new work from them.
"""
from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET

import requests

from ..models import Pin

log = logging.getLogger(__name__)

_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _pin_id(link: str, guid: str) -> str:
    match = re.search(r"/pin/(\d+)", link)
    if match:
        return match.group(1)
    return hashlib.sha1((guid or link).encode()).hexdigest()[:16]


def _full_size(url: str) -> str:
    # RSS thumbnails look like .../236x/<hash>.jpg — swap for the original.
    return re.sub(r"/\d+x(\d+)?/", "/originals/", url)


def fetch_rss_pins(feeds: list[str]) -> list[Pin]:
    pins: list[Pin] = []
    for feed in feeds:
        try:
            resp = requests.get(feed, timeout=30, headers={"User-Agent": "pinterest-collector/1.0"})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except (requests.RequestException, ET.ParseError) as exc:
            log.warning("RSS fetch failed for %s: %s", feed, exc)
            continue

        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            guid = (item.findtext("guid") or "").strip()
            desc_html = item.findtext("description") or ""

            img_match = _IMG_RE.search(desc_html)
            image_url = _full_size(img_match.group(1)) if img_match else ""
            description = _TAG_RE.sub(" ", desc_html).strip()

            pins.append(
                Pin(
                    id=_pin_id(link, guid),
                    title=title,
                    description=description,
                    image_url=image_url,
                    link=link,
                    source="rss",
                )
            )
    return pins
