"""Static HTML gallery of collected pins, with 👍/👎 feedback export.

Reads the metadata sidecars in the download directory and renders a single
self-contained `gallery.html` — no server needed, just open it in a browser.
Each card has like/dislike buttons whose state is stored in the browser's
localStorage; an "Export feedback.json" button downloads the ratings so
`python -m collector --learn` can turn them into learned preferences.
"""
from __future__ import annotations

import html
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _load_items(download_dir: Path) -> list[dict]:
    items = []
    for sidecar in download_dir.glob("*.json"):
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not (isinstance(data, dict) and data.get("id")):
            continue
        # The image sits next to its sidecar: "<id>.<ext>.json" -> "<id>.<ext>".
        image_name = sidecar.name[:-5]
        if not (download_dir / image_name).exists():
            continue
        data["_image_name"] = image_name
        items.append(data)
    items.sort(key=lambda d: float(d.get("score", 0)), reverse=True)
    return items


def _card(item: dict) -> str:
    pid = html.escape(str(item.get("id", "")))
    title = html.escape(item.get("title") or "(no title)")
    link = html.escape(item.get("link") or "#")
    image = html.escape(item["_image_name"])
    source = html.escape(item.get("source") or "")
    score = float(item.get("score", 0))
    return f"""
    <figure class="card" data-id="{pid}">
      <a href="{link}" target="_blank" rel="noopener">
        <img loading="lazy" src="{image}" alt="{title}">
      </a>
      <figcaption>
        <div class="title">{title}</div>
        <div class="meta"><span class="badge">{source}</span><span class="score">score {score:.1f}</span></div>
        <div class="rate">
          <button class="like" onclick="rate('{pid}','like')">👍</button>
          <button class="dislike" onclick="rate('{pid}','dislike')">👎</button>
        </div>
      </figcaption>
    </figure>"""


_PAGE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pinterest-collector gallery</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, sans-serif; margin: 0; background: #fafafa; color: #222; }}
  @media (prefers-color-scheme: dark) {{ body {{ background:#161616; color:#eee; }} }}
  header {{ position: sticky; top: 0; z-index: 10; padding: 12px 20px;
    background: rgba(128,128,128,.15); backdrop-filter: blur(8px);
    display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }}
  header h1 {{ font-size: 16px; margin: 0; }}
  header .stats {{ font-size: 13px; opacity: .8; }}
  button {{ cursor: pointer; border: 1px solid rgba(128,128,128,.4);
    background: transparent; color: inherit; border-radius: 8px; padding: 4px 10px; font-size: 15px; }}
  #export {{ margin-left: auto; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 14px; padding: 16px 20px; }}
  .card {{ margin: 0; background: rgba(128,128,128,.08); border-radius: 12px; overflow: hidden;
    display: flex; flex-direction: column; }}
  .card img {{ width: 100%; height: auto; display: block; aspect-ratio: 3/4; object-fit: cover; }}
  figcaption {{ padding: 8px 10px; display: flex; flex-direction: column; gap: 6px; }}
  .title {{ font-size: 13px; line-height: 1.3; max-height: 3.9em; overflow: hidden; }}
  .meta {{ display: flex; justify-content: space-between; font-size: 11px; opacity: .75; }}
  .badge {{ text-transform: uppercase; letter-spacing: .05em; }}
  .rate {{ display: flex; gap: 8px; }}
  .card.rated-like {{ outline: 2px solid #35a35a; }}
  .card.rated-dislike {{ outline: 2px solid #cc4444; opacity: .55; }}
  .rate button.active {{ background: rgba(128,128,128,.3); }}
</style>
</head>
<body>
<header>
  <h1>🖼 pinterest-collector</h1>
  <span class="stats" id="stats"></span>
  <button id="export" onclick="exportFeedback()">⬇ Export feedback.json</button>
  <button onclick="clearRatings()">Clear ratings</button>
</header>
<div class="grid">{cards}</div>
<script>
const KEY = "pc_feedback";
function ratings() {{ try {{ return JSON.parse(localStorage.getItem(KEY)) || {{}}; }} catch (e) {{ return {{}}; }} }}
function save(r) {{ localStorage.setItem(KEY, JSON.stringify(r)); }}
function rate(id, verdict) {{
  const r = ratings();
  if (r[id] === verdict) delete r[id]; else r[id] = verdict;
  save(r); render();
}}
function clearRatings() {{ if (confirm("Clear all ratings on this device?")) {{ localStorage.removeItem(KEY); render(); }} }}
function render() {{
  const r = ratings();
  let likes = 0, dislikes = 0;
  document.querySelectorAll(".card").forEach(card => {{
    const id = card.dataset.id, verdict = r[id];
    card.classList.toggle("rated-like", verdict === "like");
    card.classList.toggle("rated-dislike", verdict === "dislike");
    card.querySelector(".like").classList.toggle("active", verdict === "like");
    card.querySelector(".dislike").classList.toggle("active", verdict === "dislike");
    if (verdict === "like") likes++; else if (verdict === "dislike") dislikes++;
  }});
  const total = document.querySelectorAll(".card").length;
  document.getElementById("stats").textContent =
    total + " pins · 👍 " + likes + " · 👎 " + dislikes;
}}
function exportFeedback() {{
  const blob = new Blob([JSON.stringify(ratings(), null, 2)], {{ type: "application/json" }});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "feedback.json";
  a.click();
  URL.revokeObjectURL(a.href);
}}
render();
</script>
</body>
</html>
"""


def generate_gallery(download_dir: str | Path, output_path: str | Path) -> int:
    download_dir = Path(download_dir)
    items = _load_items(download_dir)
    cards = "".join(_card(item) for item in items)
    html_doc = _PAGE.format(cards=cards)
    Path(output_path).write_text(html_doc, encoding="utf-8")
    return len(items)
