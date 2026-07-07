"""Pinterest official API v5 source.

Requires the PINTEREST_ACCESS_TOKEN environment variable and an app with
access to the pins search scope. See README for how to obtain a token.
"""
from __future__ import annotations

import logging

import requests

from ..models import Pin

log = logging.getLogger(__name__)

API_BASE = "https://api.pinterest.com/v5"


def _best_image_url(media: dict) -> str:
    images = (media or {}).get("images") or {}
    # Prefer the original, then the largest known size.
    for key in ("originals", "1200x", "600x", "400x300", "150x150"):
        if key in images and images[key].get("url"):
            return images[key]["url"]
    for value in images.values():
        if value.get("url"):
            return value["url"]
    return ""


def fetch_api_pins(token: str, queries: list[str], per_query: int = 25) -> list[Pin]:
    pins: list[Pin] = []
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"

    for query in queries:
        try:
            resp = session.get(
                f"{API_BASE}/search/pins",
                params={"query": query, "page_size": min(per_query, 50)},
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("API search failed for %r: %s", query, exc)
            continue

        for item in resp.json().get("items", []):
            pins.append(
                Pin(
                    id=str(item.get("id", "")),
                    title=item.get("title") or "",
                    description=item.get("description") or "",
                    image_url=_best_image_url(item.get("media")),
                    link=item.get("link") or f"https://www.pinterest.com/pin/{item.get('id')}/",
                    source="api",
                )
            )
    return pins


def save_pin_to_board(token: str, pin_id: str, board_id: str) -> bool:
    """Save an existing pin to one of your boards (API: POST /pins/{id}/save)."""
    try:
        resp = requests.post(
            f"{API_BASE}/pins/{pin_id}/save",
            headers={"Authorization": f"Bearer {token}"},
            json={"board_id": str(board_id)},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.warning("Failed to save pin %s to board %s: %s", pin_id, board_id, exc)
        return False
