"""Keyword-based category assignment for gallery tabs.

Categories are defined in config.yaml:

    categories:
      - {name: "風景", keywords: ["landscape", "風景", "山", "海"]}
      - {name: "イラスト", keywords: ["illustration", "イラスト"]}

A pin gets every category whose keywords appear in its title+description
(multi-label); pins matching nothing fall into UNCATEGORIZED. Assignment
happens at gallery-generation time, so editing the config re-categorizes
everything on the next run with no migration.
"""
from __future__ import annotations

UNCATEGORIZED = "その他"


def normalize_categories(entries: list) -> list[tuple[str, list[str]]]:
    result = []
    for entry in entries or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        keywords = [str(k).lower() for k in (entry.get("keywords") or []) if str(k).strip()]
        if keywords:
            result.append((str(entry["name"]), keywords))
    return result


def categorize(text: str, categories: list[tuple[str, list[str]]]) -> list[str]:
    text = (text or "").lower()
    matched = [name for name, keywords in categories if any(k in text for k in keywords)]
    return matched or [UNCATEGORIZED]
