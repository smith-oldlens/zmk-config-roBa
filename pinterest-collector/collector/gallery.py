"""Static HTML gallery of collected pins.

Renders a single self-contained `gallery.html` — no server needed. Items
are embedded as JSON and rendered client-side, which powers:

- category tabs (auto-assigned from the `categories:` config, see
  categorize.py) with per-tab counts
- a live search box over title+description
- score / newest sort toggle
- a lightbox (click to enlarge, ←/→ to navigate, Esc to close)
- 👍/👎 feedback stored in localStorage, exportable as feedback.json for
  `python -m collector --learn`
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .categorize import categorize, normalize_categories

log = logging.getLogger(__name__)


def _load_items(download_dir: Path, categories_cfg: list) -> list[dict]:
    categories = normalize_categories(categories_cfg)
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
        image_path = download_dir / image_name
        if not image_path.exists():
            continue
        collected_at = data.get("collected_at") or ""
        if not collected_at:
            # Items downloaded before collected_at existed: use file mtime.
            collected_at = str(int(image_path.stat().st_mtime))
        items.append(
            {
                "id": str(data["id"]),
                "title": data.get("title") or "",
                "description": data.get("description") or "",
                "link": data.get("link") or "#",
                "image": image_name,
                "source": data.get("source") or "",
                "score": float(data.get("score", 0)),
                "collected_at": collected_at,
                "cats": categorize(f"{data.get('title', '')} {data.get('description', '')}", categories),
            }
        )
    items.sort(key=lambda d: d["score"], reverse=True)
    return items


_PAGE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pinterest-collector gallery</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; margin: 0; background: #fafafa; color: #222; }
  @media (prefers-color-scheme: dark) { body { background:#161616; color:#eee; } }
  header { position: sticky; top: 0; z-index: 10; padding: 10px 20px;
    background: rgba(128,128,128,.15); backdrop-filter: blur(8px);
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  header h1 { font-size: 16px; margin: 0; }
  header .stats { font-size: 13px; opacity: .8; }
  button { cursor: pointer; border: 1px solid rgba(128,128,128,.4);
    background: transparent; color: inherit; border-radius: 8px; padding: 4px 10px; font-size: 14px; }
  #search { border: 1px solid rgba(128,128,128,.4); background: transparent; color: inherit;
    border-radius: 8px; padding: 5px 10px; font-size: 14px; width: 180px; }
  #export { margin-left: auto; }
  nav.tabs { display: flex; gap: 8px; padding: 10px 20px 0; flex-wrap: wrap; }
  nav.tabs button { border-radius: 999px; }
  nav.tabs button.active { background: rgba(128,128,128,.35); font-weight: 600; }
  nav.tabs .count { opacity: .65; font-size: 12px; margin-left: 4px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 14px; padding: 16px 20px; }
  .card { margin: 0; background: rgba(128,128,128,.08); border-radius: 12px; overflow: hidden;
    display: flex; flex-direction: column; }
  .card img { width: 100%; height: auto; display: block; aspect-ratio: 3/4; object-fit: cover; cursor: zoom-in; }
  figcaption { padding: 8px 10px; display: flex; flex-direction: column; gap: 6px; }
  .title { font-size: 13px; line-height: 1.3; max-height: 3.9em; overflow: hidden; }
  .meta { display: flex; justify-content: space-between; font-size: 11px; opacity: .75; }
  .cats { font-size: 11px; opacity: .7; }
  .rate { display: flex; gap: 8px; }
  .card.rated-like { outline: 2px solid #35a35a; }
  .card.rated-dislike { outline: 2px solid #cc4444; opacity: .55; }
  .rate button.active { background: rgba(128,128,128,.3); }
  #lightbox { position: fixed; inset: 0; background: rgba(0,0,0,.85); z-index: 100;
    display: none; align-items: center; justify-content: center; flex-direction: column; gap: 10px; }
  #lightbox.open { display: flex; }
  #lightbox img { max-width: 92vw; max-height: 78vh; border-radius: 8px; }
  #lightbox .lb-bar { color: #eee; display: flex; gap: 14px; align-items: center; max-width: 92vw; }
  #lightbox .lb-bar a { color: #9cf; }
  #lightbox button { color: #eee; border-color: rgba(255,255,255,.4); }
</style>
</head>
<body>
<header>
  <h1>🖼 pinterest-collector</h1>
  <input id="search" type="search" placeholder="検索…" oninput="render()">
  <button id="sortBtn" onclick="toggleSort()">スコア順</button>
  <span class="stats" id="stats"></span>
  <button id="export" onclick="exportFeedback()">⬇ Export feedback.json</button>
  <button onclick="clearRatings()">Clear ratings</button>
</header>
<nav class="tabs" id="tabs"></nav>
<div class="grid" id="grid"></div>
<div id="lightbox" onclick="if(event.target===this)closeLightbox()">
  <img id="lb-img" alt="">
  <div class="lb-bar">
    <button onclick="lbStep(-1)">←</button>
    <span id="lb-title"></span>
    <a id="lb-link" target="_blank" rel="noopener">Pinterestで開く</a>
    <button class="like" onclick="lbRate('like')">👍</button>
    <button class="dislike" onclick="lbRate('dislike')">👎</button>
    <button onclick="lbStep(1)">→</button>
    <button onclick="closeLightbox()">✕</button>
  </div>
</div>
<script id="data" type="application/json">__ITEMS_JSON__</script>
<script>
const ITEMS = JSON.parse(document.getElementById("data").textContent);
const ALL_TAB = "すべて";
const UNCAT = "その他";
const KEY = "pc_feedback";
let activeTab = ALL_TAB;
let sortMode = "score";   // "score" | "new"
let visible = [];         // items currently shown, in display order
let lbIndex = -1;

function ratings() { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; } }
function save(r) { localStorage.setItem(KEY, JSON.stringify(r)); }
function rate(id, verdict) {
  const r = ratings();
  if (r[id] === verdict) delete r[id]; else r[id] = verdict;
  save(r); render();
}
function clearRatings() { if (confirm("Clear all ratings on this device?")) { localStorage.removeItem(KEY); render(); } }
function exportFeedback() {
  const blob = new Blob([JSON.stringify(ratings(), null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "feedback.json";
  a.click();
  URL.revokeObjectURL(a.href);
}

function tabNames() {
  const names = [];
  ITEMS.forEach(it => it.cats.forEach(c => { if (!names.includes(c)) names.push(c); }));
  // Keep the uncategorized tab last.
  const i = names.indexOf(UNCAT);
  if (i >= 0) { names.splice(i, 1); names.push(UNCAT); }
  return names;
}
function setTab(name) { activeTab = name; render(); }
function toggleSort() {
  sortMode = sortMode === "score" ? "new" : "score";
  document.getElementById("sortBtn").textContent = sortMode === "score" ? "スコア順" : "新着順";
  render();
}

function renderTabs() {
  const counts = {};
  ITEMS.forEach(it => it.cats.forEach(c => counts[c] = (counts[c] || 0) + 1));
  const tabs = [ALL_TAB, ...tabNames()];
  document.getElementById("tabs").innerHTML = tabs.map(name => {
    const n = name === ALL_TAB ? ITEMS.length : (counts[name] || 0);
    const cls = name === activeTab ? "active" : "";
    return `<button class="${cls}" onclick="setTab('${name.replace(/'/g, "\\\\'")}')">` +
      `${escapeHtml(name)}<span class="count">${n}</span></button>`;
  }).join("");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function render() {
  const r = ratings();
  const q = document.getElementById("search").value.trim().toLowerCase();
  visible = ITEMS.filter(it =>
    (activeTab === ALL_TAB || it.cats.includes(activeTab)) &&
    (!q || (it.title + " " + it.description).toLowerCase().includes(q))
  );
  visible.sort((a, b) => sortMode === "score"
    ? b.score - a.score
    : String(b.collected_at).localeCompare(String(a.collected_at)));

  document.getElementById("grid").innerHTML = visible.map((it, idx) => {
    const verdict = r[it.id] || "";
    const cls = verdict ? `rated-${verdict}` : "";
    return `<figure class="card ${cls}" data-id="${it.id}">
      <img loading="lazy" src="${escapeHtml(it.image)}" alt="${escapeHtml(it.title)}" onclick="openLightbox(${idx})">
      <figcaption>
        <div class="title">${escapeHtml(it.title) || "(no title)"}</div>
        <div class="meta"><span>${escapeHtml(it.source)}</span><span>score ${it.score.toFixed(1)}</span></div>
        <div class="cats">${it.cats.map(escapeHtml).join(" / ")}</div>
        <div class="rate">
          <button class="like ${verdict === "like" ? "active" : ""}" onclick="rate('${it.id}','like')">👍</button>
          <button class="dislike ${verdict === "dislike" ? "active" : ""}" onclick="rate('${it.id}','dislike')">👎</button>
        </div>
      </figcaption>
    </figure>`;
  }).join("");

  let likes = 0, dislikes = 0;
  Object.values(r).forEach(v => { if (v === "like") likes++; else if (v === "dislike") dislikes++; });
  document.getElementById("stats").textContent =
    `${visible.length}/${ITEMS.length} pins · 👍 ${likes} · 👎 ${dislikes}`;
  renderTabs();
}

function openLightbox(idx) {
  lbIndex = idx;
  const it = visible[idx];
  if (!it) return;
  document.getElementById("lb-img").src = it.image;
  document.getElementById("lb-title").textContent =
    `${it.title || "(no title)"} — score ${it.score.toFixed(1)}`;
  document.getElementById("lb-link").href = it.link;
  document.getElementById("lightbox").classList.add("open");
}
function closeLightbox() { document.getElementById("lightbox").classList.remove("open"); lbIndex = -1; }
function lbStep(delta) {
  if (lbIndex < 0) return;
  openLightbox((lbIndex + delta + visible.length) % visible.length);
}
function lbRate(verdict) { if (lbIndex >= 0) rate(visible[lbIndex].id, verdict); }
document.addEventListener("keydown", e => {
  if (lbIndex < 0) return;
  if (e.key === "ArrowLeft") lbStep(-1);
  else if (e.key === "ArrowRight") lbStep(1);
  else if (e.key === "Escape") closeLightbox();
});

render();
</script>
</body>
</html>
"""


def generate_gallery(
    download_dir: str | Path,
    output_path: str | Path,
    categories_cfg: list | None = None,
) -> int:
    download_dir = Path(download_dir)
    items = _load_items(download_dir, categories_cfg or [])
    items_json = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    html_doc = _PAGE.replace("__ITEMS_JSON__", items_json)
    Path(output_path).write_text(html_doc, encoding="utf-8")
    return len(items)
