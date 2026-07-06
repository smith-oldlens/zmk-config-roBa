// app.js — Scene Composer メインロジック
import * as C from './compositor.js';
import * as G from './gemini.js';

const $ = (id) => document.getElementById(id);
const stage = $('stage');

const PREVIEW_MAX = 2048;       // 編集中プレビューの最大長辺
const DEFAULT_HEIGHT_RATIO = 0.18; // 配置時の人物の高さ（背景高さ比）
const UNDO_MAX = 10;

const state = {
  bgFile: null,     // 元ファイル（書き出し時にフル解像度で再デコード）
  bgPreview: null,  // プレビュー用キャンバス（長辺 ≤ PREVIEW_MAX）
  bgFullW: 0,
  bgFullH: 0,
  layers: [],       // { id, srcId, x, y, h, flipped, colorMatch, shadowOpacity, shadowBlur, shadowDir }
  sources: new Map(), // srcId -> { canvas, sil, stats }
  matched: new Map(), // layerId -> canvas（色調マッチ済みキャッシュ）
  selectedId: null,
  placingSrcId: null, // タップ配置待ちの人物
  undoStack: [],
  seq: 1,
};

const previewScale = () => state.bgPreview.width / state.bgFullW;
const selectedLayer = () =>
  state.layers.find((l) => l.id === state.selectedId) || null;

// ---------------------------------------------------------------------------
// 背景写真
// ---------------------------------------------------------------------------

async function loadBackground(file) {
  if (!file || !file.type.startsWith('image/')) {
    toast('画像ファイルを選んでください');
    return;
  }
  if (state.layers.length &&
      !confirm('写真を変えると今の合成はクリアされます。よろしいですか？')) {
    return;
  }
  let bmp;
  try {
    try {
      bmp = await createImageBitmap(file, { imageOrientation: 'from-image' });
    } catch {
      bmp = await createImageBitmap(file); // オプション非対応ブラウザ
    }
  } catch {
    toast('この画像は読み込めませんでした');
    return;
  }
  state.bgFile = file;
  state.bgFullW = bmp.width;
  state.bgFullH = bmp.height;
  const s = Math.min(1, PREVIEW_MAX / Math.max(bmp.width, bmp.height));
  const pw = Math.round(bmp.width * s), ph = Math.round(bmp.height * s);
  state.bgPreview = C.createCanvas(pw, ph);
  state.bgPreview.getContext('2d').drawImage(bmp, 0, 0, pw, ph);
  bmp.close?.();

  state.layers = [];
  state.matched.clear();
  state.undoStack = [];
  state.selectedId = null;
  state.placingSrcId = null;

  stage.width = pw;
  stage.height = ph;
  stage.hidden = false;
  $('emptyState').hidden = true;
  $('toolbar').hidden = false;
  $('placementHint').hidden = true;
  hideSheet();
  updateUndoBtn();
  render();
}

// ---------------------------------------------------------------------------
// 描画
// ---------------------------------------------------------------------------

function resolveLayer(layer) {
  const src = state.sources.get(layer.srcId);
  return { person: state.matched.get(layer.id) || src.canvas, sil: src.sil };
}

function render() {
  if (!state.bgPreview) return;
  const ctx = stage.getContext('2d');
  const k = previewScale();
  C.renderScene(ctx, state.bgPreview, state.layers, k, resolveLayer);
  const sel = selectedLayer();
  if (sel) {
    const src = state.sources.get(sel.srcId);
    const r = C.layerRect(sel, src.canvas, k);
    ctx.save();
    ctx.strokeStyle = 'rgba(110,168,254,.95)';
    ctx.lineWidth = Math.max(1.5, stage.width / 500);
    ctx.setLineDash([8, 6]);
    ctx.strokeRect(r.x, r.y, r.w, r.h);
    ctx.restore();
  }
}

// 配置先周辺の背景統計に合わせて色調マッチ済みキャンバスを作り直す
function refreshMatch(layer) {
  const src = state.sources.get(layer.srcId);
  if (!src) return;
  if (layer.colorMatch < 0.01) {
    state.matched.delete(layer.id);
    return;
  }
  const k = previewScale();
  const scale = layer.h / src.canvas.height;
  const w = src.canvas.width * scale;
  const region = {
    x: (layer.x - w * 0.9) * k,
    y: (layer.y - layer.h * 1.15) * k,
    w: w * 1.8 * k,
    h: layer.h * 1.3 * k,
  };
  const dst = C.regionStats(state.bgPreview, region.x, region.y, region.w, region.h);
  if (!src.stats) src.stats = C.alphaStats(src.canvas);
  state.matched.set(layer.id, C.colorTransfer(src.canvas, src.stats, dst, layer.colorMatch));
}

// ---------------------------------------------------------------------------
// Undo
// ---------------------------------------------------------------------------

const snapshot = () => JSON.stringify(state.layers);

function pushUndo(snap = snapshot()) {
  state.undoStack.push(snap);
  if (state.undoStack.length > UNDO_MAX) state.undoStack.shift();
  updateUndoBtn();
}

function undo() {
  const snap = state.undoStack.pop();
  if (snap === undefined) return;
  state.layers = JSON.parse(snap);
  state.matched.clear();
  for (const l of state.layers) refreshMatch(l);
  if (!selectedLayer()) {
    state.selectedId = null;
    hideSheet();
  } else {
    syncSheet();
  }
  updateUndoBtn();
  render();
}

function updateUndoBtn() {
  $('undoBtn').disabled = state.undoStack.length === 0;
}

// ---------------------------------------------------------------------------
// レイヤー操作（配置・選択・ドラッグ・ピンチ）
// ---------------------------------------------------------------------------

function placeLayer(srcId, p) {
  pushUndo();
  const layer = {
    id: 'L' + state.seq++,
    srcId,
    x: p.x,
    y: p.y,
    h: state.bgFullH * DEFAULT_HEIGHT_RATIO,
    flipped: false,
    colorMatch: 0.6,
    shadowOpacity: 0.45,
    shadowBlur: 0.5,
    shadowDir: 0.25,
  };
  state.layers.push(layer);
  refreshMatch(layer);
  state.placingSrcId = null;
  $('placementHint').hidden = true;
  select(layer.id);
  render();
}

function select(id) {
  state.selectedId = id;
  if (id) { syncSheet(); showSheet(); } else { hideSheet(); }
  render();
}

function deleteSelected() {
  const sel = selectedLayer();
  if (!sel) return;
  pushUndo();
  state.layers = state.layers.filter((l) => l.id !== sel.id);
  state.matched.delete(sel.id);
  select(null);
}

// ステージ座標（背景フル解像度座標系）へ変換
function eventPos(e) {
  const rect = stage.getBoundingClientRect();
  const x = (e.clientX - rect.left) * (stage.width / rect.width);
  const y = (e.clientY - rect.top) * (stage.height / rect.height);
  const k = previewScale();
  return { x: x / k, y: y / k };
}

function hitTest(p) {
  let rectHit = null;
  for (let i = state.layers.length - 1; i >= 0; i--) {
    const L = state.layers[i];
    const src = state.sources.get(L.srcId).canvas;
    const scale = L.h / src.height;
    const w = src.width * scale;
    if (p.x < L.x - w / 2 || p.x > L.x + w / 2 || p.y < L.y - L.h || p.y > L.y) continue;
    rectHit = rectHit || L;
    let sx = (p.x - (L.x - w / 2)) / scale;
    if (L.flipped) sx = src.width - sx;
    const sy = (p.y - (L.y - L.h)) / scale;
    const px = Math.max(0, Math.min(src.width - 1, Math.round(sx)));
    const py = Math.max(0, Math.min(src.height - 1, Math.round(sy)));
    const a = src.getContext('2d').getImageData(px, py, 1, 1).data[3];
    if (a > 10) return L;
  }
  return rectHit; // 透明部分でも枠内なら選択できるように
}

const pointers = new Map();
let drag = null;   // { id, dx, dy, moved, before }
let pinch = null;  // { id, startDist, startH, before }

stage.addEventListener('pointerdown', (e) => {
  if (!state.bgPreview) return;
  e.preventDefault();
  stage.setPointerCapture(e.pointerId);
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

  if (pointers.size === 2 && selectedLayer()) {
    const [p1, p2] = [...pointers.values()];
    pinch = {
      id: state.selectedId,
      startDist: Math.hypot(p1.x - p2.x, p1.y - p2.y),
      startH: selectedLayer().h,
      before: drag?.before ?? snapshot(),
    };
    drag = null;
    return;
  }

  const p = eventPos(e);
  if (state.placingSrcId) {
    placeLayer(state.placingSrcId, p);
    return;
  }
  const hit = hitTest(p);
  if (hit) {
    if (state.selectedId !== hit.id) select(hit.id);
    drag = { id: hit.id, dx: hit.x - p.x, dy: hit.y - p.y, moved: false, before: snapshot() };
  } else {
    select(null);
  }
});

stage.addEventListener('pointermove', (e) => {
  if (!pointers.has(e.pointerId)) return;
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

  if (pinch && pointers.size >= 2) {
    const [p1, p2] = [...pointers.values()];
    const d = Math.hypot(p1.x - p2.x, p1.y - p2.y);
    const layer = state.layers.find((l) => l.id === pinch.id);
    if (layer && pinch.startDist > 0) {
      layer.h = clamp(pinch.startH * (d / pinch.startDist),
        state.bgFullH * 0.02, state.bgFullH * 0.9);
      syncSheet();
      render();
    }
    return;
  }

  if (drag) {
    const p = eventPos(e);
    const layer = state.layers.find((l) => l.id === drag.id);
    if (layer) {
      layer.x = clamp(p.x + drag.dx, 0, state.bgFullW);
      layer.y = clamp(p.y + drag.dy, 0, state.bgFullH);
      drag.moved = true;
      render();
    }
  }
});

function endPointer(e) {
  pointers.delete(e.pointerId);
  if (pinch && pointers.size < 2) {
    const layer = state.layers.find((l) => l.id === pinch.id);
    if (layer) refreshMatch(layer);
    pushUndo(pinch.before);
    pinch = null;
    render();
  }
  if (drag && pointers.size === 0) {
    if (drag.moved) {
      const layer = state.layers.find((l) => l.id === drag.id);
      if (layer) refreshMatch(layer);
      pushUndo(drag.before);
      render();
    }
    drag = null;
  }
}
stage.addEventListener('pointerup', endPointer);
stage.addEventListener('pointercancel', endPointer);

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

// ---------------------------------------------------------------------------
// 調整パネル
// ---------------------------------------------------------------------------

const sheet = $('adjustSheet');
function showSheet() { sheet.hidden = false; }
function hideSheet() { sheet.hidden = true; }

function syncSheet() {
  const l = selectedLayer();
  if (!l) return;
  $('sizeSlider').value = l.h / state.bgFullH;
  $('matchSlider').value = l.colorMatch;
  $('shadowOpacitySlider').value = l.shadowOpacity;
  $('shadowBlurSlider').value = l.shadowBlur;
  $('shadowDirSlider').value = l.shadowDir;
}

let sliderBefore = null; // スライダー操作開始前のスナップショット

function bindSlider(id, apply, needsMatch = false) {
  const el = $(id);
  el.addEventListener('input', () => {
    const l = selectedLayer();
    if (!l) return;
    if (sliderBefore === null) sliderBefore = snapshot();
    apply(l, parseFloat(el.value));
    render();
  });
  el.addEventListener('change', () => {
    const l = selectedLayer();
    if (!l) return;
    if (needsMatch) refreshMatch(l);
    if (sliderBefore !== null) { pushUndo(sliderBefore); sliderBefore = null; }
    render();
  });
}

bindSlider('sizeSlider', (l, v) => { l.h = state.bgFullH * v; }, true);
bindSlider('matchSlider', (l, v) => { l.colorMatch = v; }, true);
bindSlider('shadowOpacitySlider', (l, v) => { l.shadowOpacity = v; });
bindSlider('shadowBlurSlider', (l, v) => { l.shadowBlur = v; });
bindSlider('shadowDirSlider', (l, v) => { l.shadowDir = v; });

$('flipBtn').addEventListener('click', () => {
  const l = selectedLayer();
  if (!l) return;
  pushUndo();
  l.flipped = !l.flipped;
  render();
});
$('deleteBtn').addEventListener('click', deleteSelected);
$('closeAdjustBtn').addEventListener('click', () => select(null));

// ---------------------------------------------------------------------------
// 人物ソース管理・IndexedDB ライブラリ
// ---------------------------------------------------------------------------

function addSource(id, canvas) {
  state.sources.set(id, { canvas, sil: C.makeSilhouette(canvas), stats: null });
}

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('scene-composer', 1);
    req.onupgradeneeded = () => req.result.createObjectStore('persons', { keyPath: 'id' });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function dbRun(mode, fn) {
  const db = await openDb();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction('persons', mode);
      const out = fn(tx.objectStore('persons'));
      tx.oncomplete = () => resolve(out.result ?? out);
      tx.onerror = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

const dbPut = (rec) => dbRun('readwrite', (s) => s.put(rec));
const dbDelete = (id) => dbRun('readwrite', (s) => s.delete(id));
const dbAll = () => dbRun('readonly', (s) => s.getAll());

async function savePersonToLibrary(id, canvas, prompt) {
  try {
    const blob = await new Promise((r) => canvas.toBlob(r, 'image/png'));
    if (blob) await dbPut({ id, blob, prompt, createdAt: Date.now() });
  } catch {
    // ライブラリ保存に失敗しても配置はできるので握りつぶす
  }
}

async function loadSourceFromLibrary(rec) {
  if (state.sources.has(rec.id)) return;
  const bmp = await createImageBitmap(rec.blob);
  const canvas = C.canvasFrom(bmp);
  bmp.close?.();
  addSource(rec.id, canvas);
}

async function renderLibrary() {
  const grid = $('libGrid');
  grid.textContent = '';
  let recs = [];
  try { recs = await dbAll(); } catch { /* IndexedDB 不可の環境 */ }
  recs.sort((a, b) => b.createdAt - a.createdAt);
  $('libEmpty').hidden = recs.length > 0;
  for (const rec of recs) {
    const item = document.createElement('div');
    item.className = 'lib-item';
    const img = document.createElement('img');
    img.src = URL.createObjectURL(rec.blob);
    img.alt = rec.prompt || '人物';
    item.appendChild(img);
    const del = document.createElement('button');
    del.className = 'del';
    del.textContent = '×';
    del.addEventListener('click', async (e) => {
      e.stopPropagation();
      await dbDelete(rec.id).catch(() => {});
      renderLibrary();
    });
    item.appendChild(del);
    item.addEventListener('click', async () => {
      try {
        await loadSourceFromLibrary(rec);
      } catch {
        toast('この人物を読み込めませんでした');
        return;
      }
      startPlacing(rec.id);
      closeModal('libModal');
    });
    grid.appendChild(item);
  }
}

function startPlacing(srcId) {
  if (!state.bgPreview) {
    toast('先に背景写真を選んでください');
    return;
  }
  state.placingSrcId = srcId;
  select(null);
  $('placementHint').hidden = false;
}

// ---------------------------------------------------------------------------
// 人物生成モーダル
// ---------------------------------------------------------------------------

let genRaw = null;    // 生成された元画像（緑背景）
let genKeyed = null;  // 透過処理後
let genKeyColor = null; // null = 緑背景アルゴリズム
let genPrompt = '';
let eyedropping = false;

function initPresets() {
  const wrap = $('presetChips');
  for (const p of G.PRESETS) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = p.label;
    b.addEventListener('click', () => {
      $('promptInput').value = p.prompt;
      [...wrap.children].forEach((c) => c.classList.toggle('active', c === b));
    });
    wrap.appendChild(b);
  }
}

async function runGenerate() {
  const apiKey = G.getApiKey();
  if (!apiKey) {
    toast('先に Gemini APIキーを設定してください');
    openModal('settingsModal');
    return;
  }
  const prompt = $('promptInput').value.trim();
  if (!prompt) {
    toast('どんな人物か入力するか、プリセットを選んでください');
    return;
  }
  const btn = $('runGenBtn');
  const status = $('genStatus');
  btn.disabled = true;
  status.hidden = false;
  status.classList.remove('error');
  status.textContent = '生成中…（数秒〜十数秒かかります）';
  $('genResult').hidden = true;
  try {
    const dataUrl = await G.generatePerson(apiKey, prompt);
    const img = new Image();
    await new Promise((res, rej) => {
      img.onload = res;
      img.onerror = () => rej(new Error('生成画像を読み込めませんでした'));
      img.src = dataUrl;
    });
    genRaw = C.createCanvas(img.naturalWidth, img.naturalHeight);
    genRaw.getContext('2d').drawImage(img, 0, 0);
    genKeyColor = null;
    genPrompt = prompt;
    $('keyToleranceSlider').value = 60;
    applyKeying();
    $('genResult').hidden = false;
    $('rawWrap').hidden = true;
    $('eyedropHint').hidden = true;
    status.hidden = true;
  } catch (err) {
    status.textContent = err.message || String(err);
    status.classList.add('error');
  } finally {
    btn.disabled = false;
  }
}

function applyKeying() {
  if (!genRaw) return;
  const tol = parseFloat($('keyToleranceSlider').value);
  genKeyed = genKeyColor
    ? C.keyByColor(genRaw, genKeyColor, tol, 40)
    : C.chromaKeyGreen(genRaw, { low: tol * 0.2, high: tol });
  const pv = $('genPreview');
  pv.width = genKeyed.width;
  pv.height = genKeyed.height;
  const ctx = pv.getContext('2d');
  ctx.clearRect(0, 0, pv.width, pv.height);
  ctx.drawImage(genKeyed, 0, 0);
}

function placeGenerated() {
  if (!genKeyed) return;
  const trimmed = C.trimAlpha(genKeyed);
  if (trimmed.width < 4 || trimmed.height < 4) {
    toast('人物を切り抜けませんでした。抜き具合を調整してください');
    return;
  }
  const id = 'P' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  addSource(id, trimmed);
  savePersonToLibrary(id, trimmed, genPrompt);
  closeModal('genModal');
  startPlacing(id);
}

$('runGenBtn').addEventListener('click', runGenerate);
$('placeGenBtn').addEventListener('click', placeGenerated);
$('discardGenBtn').addEventListener('click', () => {
  genRaw = genKeyed = null;
  $('genResult').hidden = true;
});
$('keyToleranceSlider').addEventListener('input', applyKeying);

$('eyedropperBtn').addEventListener('click', () => {
  if (!genRaw) return;
  const raw = $('rawPreview');
  raw.width = genRaw.width;
  raw.height = genRaw.height;
  raw.getContext('2d').drawImage(genRaw, 0, 0);
  $('rawWrap').hidden = false;
  $('eyedropHint').hidden = false;
  eyedropping = true;
});

$('rawPreview').addEventListener('click', (e) => {
  if (!eyedropping || !genRaw) return;
  const raw = $('rawPreview');
  const rect = raw.getBoundingClientRect();
  const x = Math.round((e.clientX - rect.left) * (raw.width / rect.width));
  const y = Math.round((e.clientY - rect.top) * (raw.height / rect.height));
  const d = genRaw.getContext('2d').getImageData(
    clamp(x, 0, genRaw.width - 1), clamp(y, 0, genRaw.height - 1), 1, 1).data;
  genKeyColor = { r: d[0], g: d[1], b: d[2] };
  eyedropping = false;
  $('eyedropHint').hidden = true;
  applyKeying();
});

// ---------------------------------------------------------------------------
// 書き出し
// ---------------------------------------------------------------------------

async function renderFull() {
  let bmp;
  try {
    bmp = await createImageBitmap(state.bgFile, { imageOrientation: 'from-image' });
  } catch {
    bmp = await createImageBitmap(state.bgFile);
  }
  const cv = C.createCanvas(bmp.width, bmp.height);
  const ctx = cv.getContext('2d');
  ctx.drawImage(bmp, 0, 0);
  bmp.close?.();
  for (const layer of state.layers) {
    const { person, sil } = resolveLayer(layer);
    C.drawLayer(ctx, layer, person, sil, 1);
  }
  return cv;
}

async function exportImage(type) {
  const status = $('exportStatus');
  status.hidden = false;
  status.classList.remove('error');
  status.textContent = '書き出し中…';
  try {
    const cv = await renderFull();
    const mime = type === 'png' ? 'image/png' : 'image/jpeg';
    const blob = await new Promise((r) => cv.toBlob(r, mime, 0.92));
    if (!blob) throw new Error('書き出しに失敗しました');
    const name = `scene-${timestamp()}.${type === 'png' ? 'png' : 'jpg'}`;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 30000);
    status.textContent = `保存しました（${cv.width}×${cv.height}）`;
  } catch (err) {
    status.textContent = err.message || String(err);
    status.classList.add('error');
  }
}

async function shareImage() {
  const status = $('exportStatus');
  status.hidden = false;
  status.classList.remove('error');
  status.textContent = '準備中…';
  try {
    const cv = await renderFull();
    const blob = await new Promise((r) => cv.toBlob(r, 'image/jpeg', 0.92));
    const file = new File([blob], `scene-${timestamp()}.jpg`, { type: 'image/jpeg' });
    await navigator.share({ files: [file] });
    status.hidden = true;
  } catch (err) {
    if (err.name !== 'AbortError') {
      status.textContent = '共有できませんでした';
      status.classList.add('error');
    } else {
      status.hidden = true;
    }
  }
}

function timestamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

$('exportJpegBtn').addEventListener('click', () => exportImage('jpeg'));
$('exportPngBtn').addEventListener('click', () => exportImage('png'));
$('shareBtn').addEventListener('click', shareImage);

// ---------------------------------------------------------------------------
// モーダル・トースト・その他 UI 配線
// ---------------------------------------------------------------------------

function openModal(id) { $(id).hidden = false; }
function closeModal(id) { $(id).hidden = true; }

document.querySelectorAll('.modal').forEach((m) => {
  m.addEventListener('click', (e) => { if (e.target === m) m.hidden = true; });
});
document.querySelectorAll('[data-close]').forEach((b) => {
  b.addEventListener('click', () => closeModal(b.dataset.close));
});

let toastTimer = null;
function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3000);
}

$('pickBgBtn').addEventListener('click', () => $('bgInput').click());
$('changeBgBtn').addEventListener('click', () => $('bgInput').click());
$('bgInput').addEventListener('change', (e) => {
  if (e.target.files[0]) loadBackground(e.target.files[0]);
  e.target.value = '';
});

const stageWrap = $('stageWrap');
stageWrap.addEventListener('dragover', (e) => e.preventDefault());
stageWrap.addEventListener('drop', (e) => {
  e.preventDefault();
  const f = e.dataTransfer.files?.[0];
  if (f) loadBackground(f);
});

$('genBtn').addEventListener('click', () => {
  $('genStatus').hidden = true;
  openModal('genModal');
});
$('libBtn').addEventListener('click', () => {
  renderLibrary();
  openModal('libModal');
});
$('exportBtn').addEventListener('click', () => {
  if (!state.layers.length) toast('まだ人物が配置されていません');
  $('exportStatus').hidden = true;
  openModal('exportModal');
});
$('undoBtn').addEventListener('click', undo);

$('settingsBtn').addEventListener('click', () => {
  $('apiKeyInput').value = G.getApiKey();
  openModal('settingsModal');
});
$('saveKeyBtn').addEventListener('click', () => {
  G.setApiKey($('apiKeyInput').value);
  closeModal('settingsModal');
  toast('APIキーを保存しました');
});
$('clearKeyBtn').addEventListener('click', () => {
  G.setApiKey('');
  $('apiKeyInput').value = '';
  toast('APIキーを削除しました');
});

// navigator.share が使えれば共有ボタンを出す
try {
  const probe = new File([new Blob(['x'])], 'x.jpg', { type: 'image/jpeg' });
  if (navigator.canShare?.({ files: [probe] })) $('shareBtn').hidden = false;
} catch { /* 非対応 */ }

initPresets();

// テストから内部状態を確認できるように公開（本番動作には影響しない）
window.__scene = { state, render, refreshMatch, loadBackground };
