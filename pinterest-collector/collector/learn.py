"""Feedback-driven preference learning.

The HTML gallery lets you mark collected pins 👍 / 👎 and export those
ratings as `feedback.json`. This module reads that file together with the
metadata sidecars written by the downloader, finds the words that best
distinguish liked pins from disliked ones, and accumulates them into
`learned.json` as extra like/dislike keywords.

Learned keywords are kept in their own file so your hand-written
`config.yaml` (with its comments) is never rewritten. At scoring time the
learned keywords are merged on top of the config preferences.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)

# English word tokens (3+ letters) and runs of Japanese characters (2+).
_EN_RE = re.compile(r"[a-z][a-z'\-]{2,}")
_JA_RE = re.compile(r"[぀-ゟ゠-ヿ一-鿿]{2,}")

_STOP = {
    "the", "and", "for", "with", "this", "that", "from", "your", "you", "are",
    "was", "but", "not", "have", "has", "will", "can", "all", "out", "new",
    "how", "why", "what", "when", "who", "get", "one", "two", "more", "other",
    "some", "any", "its", "his", "her", "them", "they", "our", "about", "into",
    "over", "also", "best", "top", "diy", "etc", "com", "www", "http", "https",
    "png", "jpg", "jpeg", "pinterest", "pin", "photo", "image", "images",
}

_MAX_LEARNED_WEIGHT = 5.0
_MAX_TERMS_PER_SIDE = 15


def _tokens(text: str) -> set[str]:
    text = text.lower()
    toks = set(_EN_RE.findall(text)) | set(_JA_RE.findall(text))
    return {t for t in toks if t not in _STOP}


def load_learned(path: str | Path) -> dict:
    path = Path(path)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {
                "like_keywords": dict(data.get("like_keywords", {})),
                "dislike_keywords": dict(data.get("dislike_keywords", {})),
            }
        except (json.JSONDecodeError, OSError):
            log.warning("Could not parse learned keywords at %s; ignoring.", path)
    return {"like_keywords": {}, "dislike_keywords": {}}


def _save_learned(path: str | Path, learned: dict) -> None:
    Path(path).write_text(
        json.dumps(learned, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def apply_learned_to_prefs(prefs: dict, learned: dict) -> dict:
    """Merge learned keywords on top of config preferences (config wins on ties)."""
    merged = dict(prefs)

    def _existing_words(entries: list) -> set[str]:
        words = set()
        for entry in entries or []:
            if isinstance(entry, str):
                words.add(entry.lower())
            elif isinstance(entry, dict) and entry.get("word"):
                words.add(str(entry["word"]).lower())
        return words

    for side in ("like_keywords", "dislike_keywords"):
        base = list(prefs.get(side) or [])
        have = _existing_words(base)
        for word, weight in (learned.get(side) or {}).items():
            if word.lower() not in have:
                base.append({"word": word, "weight": weight})
        merged[side] = base
    return merged


def _metadata_by_id(download_dir: str | Path) -> dict:
    meta: dict[str, dict] = {}
    for sidecar in Path(download_dir).glob("*.json"):
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict) and data.get("id"):
            meta[str(data["id"])] = data
    return meta


def learn_from_feedback(cfg: dict) -> int:
    feedback_path = Path(cfg["feedback_file"])
    if not feedback_path.exists():
        log.warning(
            "No feedback file at %s. Open the gallery, rate some pins, then "
            "click 'Export feedback.json' and save it there.",
            feedback_path,
        )
        return 1

    try:
        feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Could not read feedback file %s: %s", feedback_path, exc)
        return 1

    meta = _metadata_by_id(cfg["output"]["download_dir"])
    liked_docs: list[set[str]] = []
    disliked_docs: list[set[str]] = []
    for pin_id, rating in feedback.items():
        data = meta.get(str(pin_id))
        text = f"{data.get('title', '')} {data.get('description', '')}" if data else ""
        toks = _tokens(text)
        if not toks:
            continue
        if rating == "like":
            liked_docs.append(toks)
        elif rating == "dislike":
            disliked_docs.append(toks)

    n_like, n_dis = len(liked_docs), len(disliked_docs)
    if n_like == 0 and n_dis == 0:
        log.warning("Feedback contained no ratings with usable metadata; nothing to learn.")
        return 1
    log.info("Learning from %d liked and %d disliked pins.", n_like, n_dis)

    like_df, dis_df = Counter(), Counter()
    for toks in liked_docs:
        like_df.update(toks)
    for toks in disliked_docs:
        dis_df.update(toks)

    def _min_support(total: int) -> int:
        return 2 if total >= 3 else 1

    like_candidates = []
    dislike_candidates = []
    for term in set(like_df) | set(dis_df):
        p_like = like_df[term] / n_like if n_like else 0.0
        p_dis = dis_df[term] / n_dis if n_dis else 0.0
        dist = p_like - p_dis
        weight = round(min(0.5 + abs(dist) * 1.5, _MAX_LEARNED_WEIGHT), 1)
        if dist > 0.05 and like_df[term] >= _min_support(n_like):
            like_candidates.append((term, dist, weight))
        elif dist < -0.05 and dis_df[term] >= _min_support(n_dis):
            dislike_candidates.append((term, dist, weight))

    like_candidates.sort(key=lambda x: x[1], reverse=True)
    dislike_candidates.sort(key=lambda x: x[1])
    like_candidates = like_candidates[:_MAX_TERMS_PER_SIDE]
    dislike_candidates = dislike_candidates[:_MAX_TERMS_PER_SIDE]

    learned = load_learned(cfg["learned_file"])
    # Idempotent: re-running on the same feedback keeps the strongest signal
    # rather than compounding weights every time.
    for term, _dist, weight in like_candidates:
        learned["like_keywords"][term] = max(learned["like_keywords"].get(term, 0.0), weight)
    for term, _dist, weight in dislike_candidates:
        learned["dislike_keywords"][term] = max(learned["dislike_keywords"].get(term, 0.0), weight)

    _save_learned(cfg["learned_file"], learned)

    log.info(
        "Learned %d like / %d dislike keywords (total now %d / %d). Saved to %s",
        len(like_candidates),
        len(dislike_candidates),
        len(learned["like_keywords"]),
        len(learned["dislike_keywords"]),
        cfg["learned_file"],
    )
    if like_candidates:
        log.info("  liked terms:    %s", ", ".join(t for t, _, _ in like_candidates))
    if dislike_candidates:
        log.info("  disliked terms: %s", ", ".join(t for t, _, _ in dislike_candidates))
    return 0
