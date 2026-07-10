// app.js — Scene Composer メインロジック
import * as C from './compositor.js';
import * as P from './providers.js';

const $ = (id) => document.getElementById(id);
const stage = $('stage');

const PREVIEW_MAX = 2048;       // 編集中プレビューの最大長辺
const DEFAULT_HEIGHT_RATIO = 0.18; // 配置時の人物の高さ（背景高さ比）
const MIN_HEIGHT_RATIO = 0.03;  // 人物の高さの下限（背景高さ比）
const MAX_HEIGHT_RATIO = 0.9;   // 人物の高さの上限（背景高さ比）
const UNDO_MAX = 10;
// 書き出し解像度の上限（端末のCanvas制限で真っ黒/失敗になるのを防ぐ）
const MAX_EXPORT_AREA = 16777216; // 約16.7Mpx（iOS Safari の安全圏）
const MAX_EXPORT_DIM = 8192;

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

// ドラッグ/ピンチ/スライダー中は毎イベント描画せず、1フレームに1回へ間引く
let renderPending = false;
function scheduleRender() {
  if (renderPending) return;
  renderPending = true;
  requestAnimationFrame(() => { renderPending = false; render(); });
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
  sliderBefore = null; // 選択が変わったらスライダーの途中スナップショットは破棄
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
let drag = null;   // { pointerId, id, dx, dy, moved, before }
let pinch = null;  // { id, startDist, startH, moved, before }

stage.addEventListener('pointerdown', (e) => {
  if (!state.bgPreview) return;
  e.preventDefault();
  stage.setPointerCapture(e.pointerId);
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

  // 2本目の指が乗ったらピンチ開始。まだ何も選択されていなければ、
  // どちらかの指の位置にあるレイヤーを掴んでからピンチする（掴み損ね対策）。
  if (pointers.size === 2 && !state.placingSrcId) {
    if (!selectedLayer()) {
      const hit = hitTest(eventPos(e));
      if (hit) select(hit.id);
    }
    if (selectedLayer()) {
      const [p1, p2] = [...pointers.values()];
      pinch = {
        id: state.selectedId,
        startDist: Math.hypot(p1.x - p2.x, p1.y - p2.y),
        startH: selectedLayer().h,
        moved: false,
        before: drag?.before ?? snapshot(),
      };
      drag = null;
      return;
    }
  }

  const p = eventPos(e);
  if (state.placingSrcId) {
    placeLayer(state.placingSrcId, p);
    return;
  }
  const hit = hitTest(p);
  if (hit) {
    if (state.selectedId !== hit.id) select(hit.id);
    drag = { pointerId: e.pointerId, id: hit.id, dx: hit.x - p.x, dy: hit.y - p.y, moved: false, before: snapshot() };
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
        state.bgFullH * MIN_HEIGHT_RATIO, state.bgFullH * MAX_HEIGHT_RATIO);
      pinch.moved = true;
      syncSheet();
      scheduleRender();
    }
    return;
  }

  // ドラッグは開始した指だけが動かす（マルチタッチでの暴れを防ぐ）
  if (drag && e.pointerId === drag.pointerId) {
    const p = eventPos(e);
    const layer = state.layers.find((l) => l.id === drag.id);
    if (layer) {
      layer.x = clamp(p.x + drag.dx, 0, state.bgFullW);
      layer.y = clamp(p.y + drag.dy, 0, state.bgFullH);
      drag.moved = true;
      scheduleRender();
    }
  }
});

function endPointer(e) {
  pointers.delete(e.pointerId);
  if (pinch && pointers.size < 2) {
    if (pinch.moved) {
      const layer = state.layers.find((l) => l.id === pinch.id);
      if (layer) refreshMatch(layer);
      pushUndo(pinch.before);
    }
    pinch = null;
    render();
  }
  if (drag && (e.pointerId === drag.pointerId || pointers.size === 0)) {
    if (drag.moved) {
      const layer = state.layers.find((l) => l.id === drag.id);
      if (layer) refreshMatch(layer);
      pushUndo(drag.before);
    }
    drag = null;
    render();
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
    scheduleRender();
  });
  el.addEventListener('change', () => {
    // change 前にレイヤーが消えても sliderBefore は必ずクリアする
    // （残すと次の別レイヤー操作で古いスナップショットが Undo に積まれる）
    const before = sliderBefore;
    sliderBefore = null;
    const l = selectedLayer();
    if (!l) return;
    if (needsMatch) refreshMatch(l);
    if (before !== null) pushUndo(before);
    render();
  });
}

// スライダーの範囲は定数から設定（ピンチ側のクランプ値と食い違わないように一元化）
$('sizeSlider').min = MIN_HEIGHT_RATIO;
$('sizeSlider').max = MAX_HEIGHT_RATIO;

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

let libUrls = []; // 前回のライブラリ描画で作った object URL（再描画時に解放する）

async function renderLibrary() {
  const grid = $('libGrid');
  grid.textContent = '';
  libUrls.forEach((u) => URL.revokeObjectURL(u));
  libUrls = [];
  let recs = [];
  try { recs = await dbAll(); } catch { /* IndexedDB 不可の環境 */ }
  recs.sort((a, b) => b.createdAt - a.createdAt);
  $('libEmpty').hidden = recs.length > 0;
  for (const rec of recs) {
    const item = document.createElement('div');
    item.className = 'lib-item';
    const img = document.createElement('img');
    const url = URL.createObjectURL(rec.blob);
    libUrls.push(url);
    img.src = url;
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

let genRaw = null;    // 生成された元画像
let genKeyed = null;  // 透過処理後
let genKeyColor = null; // null = 緑背景アルゴリズム
let genPrompt = '';
let genTransparent = false; // プロバイダーが透過PNGを直接返したか
let eyedropping = false;

function initPresets() {
  const wrap = $('presetChips');
  for (const p of P.PRESETS) {
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
  const provider = P.getProvider();
  if (!provider.noApiKey && !P.getApiKey(provider.id)) {
    toast(`先に ${provider.label} のAPIキーを設定してください`);
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
    const dataUrl = await P.generatePerson(prompt);
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
    genTransparent = !!provider.transparentOutput;
    // 透過PNGを直接返すプロバイダーではクロマキー調整UIは不要
    $('keyTools').hidden = genTransparent;
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

// キャンバスに透明ピクセルが含まれるか（取り込んだ画像が切り抜き済みかの判定）
function hasTransparency(canvas) {
  const d = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
  for (let i = 3; i < d.length; i += 4) if (d[i] < 250) return true;
  return false;
}

// 手持ちの画像ファイルを取り込む（API不要・完全無料の経路）
async function importPersonImage(file) {
  if (!file || !file.type.startsWith('image/')) {
    toast('画像ファイルを選んでください');
    return;
  }
  let bmp;
  try {
    bmp = await createImageBitmap(file);
  } catch {
    toast('この画像は読み込めませんでした');
    return;
  }
  genRaw = C.canvasFrom(bmp);
  bmp.close?.();
  genPrompt = file.name.replace(/\.[^.]+$/, '') || '取り込み画像';
  genKeyColor = null;
  // 透過PNGならそのまま使う。背景つき写真ならクロマキー/スポイトで抜く。
  genTransparent = hasTransparency(genRaw);
  $('keyTools').hidden = genTransparent;
  $('keyToleranceSlider').value = 60;
  applyKeying();
  $('genResult').hidden = false;
  $('rawWrap').hidden = true;
  $('eyedropHint').hidden = true;
  $('genStatus').hidden = true;
  if (!genTransparent) {
    toast('背景を抜くには「切り抜きの調整」→「背景色を選び直す」で背景をタップ');
  }
}

function applyKeying() {
  if (!genRaw) return;
  if (genTransparent) {
    // 既に透過済み。そのまま使う。
    genKeyed = C.canvasFrom(genRaw);
  } else {
    const tol = parseFloat($('keyToleranceSlider').value);
    genKeyed = genKeyColor
      ? C.keyByColor(genRaw, genKeyColor, tol, 40)
      : C.chromaKeyGreen(genRaw, { low: tol * 0.2, high: tol });
  }
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
$('importBtn').addEventListener('click', () => $('importInput').click());
$('importInput').addEventListener('change', (e) => {
  if (e.target.files[0]) importPersonImage(e.target.files[0]);
  e.target.value = '';
});

// クリップボードの画像を取り込む（外部AIで生成→コピー→ここで貼り付けの近道）
$('pasteImportBtn').addEventListener('click', async () => {
  try {
    const items = await navigator.clipboard.read();
    for (const item of items) {
      const type = item.types.find((t) => t.startsWith('image/'));
      if (type) {
        const blob = await item.getType(type);
        importPersonImage(new File([blob], 'clipboard.png', { type }));
        return;
      }
    }
    toast('クリップボードに画像がありません。先に画像をコピーしてください');
  } catch {
    toast('貼り付けが許可されませんでした。Ctrl/⌘+V でも取り込めます');
  }
});

// Ctrl/⌘+V での貼り付け：生成モーダルが開いていれば人物、それ以外は背景として扱う
window.addEventListener('paste', (e) => {
  const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith('image/'));
  if (!item) return;
  const file = item.getAsFile();
  if (!file) return;
  e.preventDefault();
  if (!$('genModal').hidden) importPersonImage(file);
  else loadBackground(file);
});

// 外部AI用プロンプト（緑背景・全身の指定込み）をコピー
function externalPrompt() {
  const desc = $('promptInput').value.trim() || P.PRESETS[0].prompt;
  return `${desc} 全身が頭のてっぺんからつま先まで完全に写っている立ち姿で、見切れていない。` +
    '人物は画面の中央。背景は完全に均一で鮮やかなクロマキー用の緑一色（#00FF00）。' +
    '背景に影を落とさない。柔らかい自然光。写実的な写真。';
}
$('copyPromptBtn').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(externalPrompt());
    toast('プロンプトをコピーしました。AIに貼り付けて生成してください');
  } catch {
    window.prompt('以下をコピーしてください', externalPrompt());
  }
});

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

async function decodeBackground() {
  try {
    return await createImageBitmap(state.bgFile, { imageOrientation: 'from-image' });
  } catch {
    return createImageBitmap(state.bgFile);
  }
}

// フル解像度で合成。端末のCanvas制限を超えそうなら安全な範囲へ縮小する。
// 戻り値: { cv, scaled }（scaled=true なら元解像度から縮小された）
async function renderFull() {
  const bmp = await decodeBackground();
  let s = 1;
  const area = bmp.width * bmp.height;
  if (area > MAX_EXPORT_AREA) s = Math.sqrt(MAX_EXPORT_AREA / area);
  const longEdge = Math.max(bmp.width, bmp.height) * s;
  if (longEdge > MAX_EXPORT_DIM) s *= MAX_EXPORT_DIM / longEdge;

  const W = Math.max(1, Math.round(bmp.width * s));
  const H = Math.max(1, Math.round(bmp.height * s));
  const cv = C.createCanvas(W, H);
  const ctx = cv.getContext('2d');
  ctx.drawImage(bmp, 0, 0, W, H);
  bmp.close?.();
  for (const layer of state.layers) {
    const { person, sil } = resolveLayer(layer);
    C.drawLayer(ctx, layer, person, sil, s);
  }
  return { cv, scaled: s < 0.999 };
}

// 書き出し用の Blob を用意（保存・共有で共通）
async function prepareBlob(type) {
  const { cv, scaled } = await renderFull();
  const mime = type === 'png' ? 'image/png' : 'image/jpeg';
  const ext = type === 'png' ? 'png' : 'jpg';
  const blob = await new Promise((r) => cv.toBlob(r, mime, 0.92));
  if (!blob) throw new Error('書き出しに失敗しました（画像が大きすぎる可能性があります）。');
  return { blob, name: `scene-${timestamp()}.${ext}`, w: cv.width, h: cv.height, scaled };
}

async function exportImage(type) {
  const status = $('exportStatus');
  status.hidden = false;
  status.classList.remove('error');
  status.textContent = '書き出し中…';
  try {
    const { blob, name, w, h, scaled } = await prepareBlob(type);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 30000);
    status.textContent = scaled
      ? `保存しました（端末制限のため ${w}×${h} に縮小）`
      : `保存しました（${w}×${h}）`;
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
    const { blob, name } = await prepareBlob('jpeg');
    const file = new File([blob], name, { type: 'image/jpeg' });
    await navigator.share({ files: [file] });
    status.hidden = true;
  } catch (err) {
    if (err.name === 'AbortError') {
      status.hidden = true;
    } else {
      status.textContent = '共有できませんでした。「JPEGで保存」をお試しください。';
      status.classList.add('error');
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

// ウィンドウ全体でドロップの既定動作（ファイルへ画面遷移）を止める。
// これがないと、UIのどこか（ツールバーやモーダル上など）に画像を落とした瞬間に
// ブラウザがそのファイルを開いてしまい、編集中のセッションが丸ごと失われる。
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => {
  e.preventDefault();
  const f = e.dataTransfer?.files?.[0];
  if (f && f.type.startsWith('image/')) loadBackground(f);
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
  if (!state.layers.length) { toast('まだ人物が配置されていません'); return; }
  $('exportStatus').hidden = true;
  openModal('exportModal');
});
$('undoBtn').addEventListener('click', undo);

// --- 設定モーダル（プロバイダー選択 + APIキー） ---
function initProviderSelect() {
  const sel = $('providerSelect');
  for (const p of P.PROVIDERS) {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = p.label;
    sel.appendChild(opt);
  }
  sel.addEventListener('change', () => syncSettingsForProvider(sel.value));
}

function syncSettingsForProvider(id) {
  const provider = P.getProvider(id);
  const keyless = !!provider.noApiKey;
  // キー不要のサービスではAPIキー欄・削除ボタンを隠す
  $('apiKeyLabel').hidden = keyless;
  $('clearKeyBtn').hidden = keyless;
  if (keyless) {
    $('keyHelp').innerHTML = 'このサービスは <b>APIキー不要・無料</b> で使えます。そのまま「人物を生成」できます。';
  } else {
    $('apiKeyInput').value = P.getApiKey(id);
    $('apiKeyInput').placeholder = provider.keyPlaceholder || '';
    $('apiKeyLabel').childNodes[0].nodeValue = `${provider.label} のAPIキー`;
    $('keyHelp').innerHTML =
      `キーは <a href="${provider.keyHelpUrl}" target="_blank" rel="noopener">${provider.keyHelpLabel}</a> で取得できます。`;
  }
}

$('settingsBtn').addEventListener('click', () => {
  const id = P.getProviderId();
  $('providerSelect').value = id;
  syncSettingsForProvider(id);
  openModal('settingsModal');
});
$('saveKeyBtn').addEventListener('click', () => {
  const id = $('providerSelect').value;
  P.setProviderId(id);
  if (!P.getProvider(id).noApiKey) P.setApiKey(id, $('apiKeyInput').value);
  closeModal('settingsModal');
  toast('設定を保存しました');
});
$('clearKeyBtn').addEventListener('click', () => {
  const id = $('providerSelect').value;
  P.setApiKey(id, '');
  $('apiKeyInput').value = '';
  toast('APIキーを削除しました');
});

// navigator.share が使えれば共有ボタンを出す
try {
  const probe = new File([new Blob(['x'])], 'x.jpg', { type: 'image/jpeg' });
  if (navigator.canShare?.({ files: [probe] })) $('shareBtn').hidden = false;
} catch { /* 非対応 */ }

initPresets();
initProviderSelect();

// --- PWA（ホーム画面に追加・オフライン・共有シート受け取り） ---
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('./sw.js').catch(() => { /* 非対応環境では無視 */ });
}

// 共有シート経由で開かれた場合：Service Worker が一時保存した画像を受け取る
let sharedFile = null;
if (new URLSearchParams(location.search).has('shared')) {
  (async () => {
    try {
      const cache = await caches.open('scene-composer-shared');
      const res = await cache.match('shared-image');
      if (res) {
        const blob = await res.blob();
        await cache.delete('shared-image');
        sharedFile = new File([blob], 'shared.png', { type: blob.type || 'image/png' });
        openModal('shareModal');
      }
    } catch { /* 取得できなければ通常起動 */ }
    history.replaceState(null, '', location.pathname);
  })();
}
$('sharedAsPersonBtn').addEventListener('click', () => {
  closeModal('shareModal');
  if (!sharedFile) return;
  openModal('genModal');
  importPersonImage(sharedFile);
  sharedFile = null;
});
$('sharedAsBgBtn').addEventListener('click', () => {
  closeModal('shareModal');
  if (!sharedFile) return;
  loadBackground(sharedFile);
  sharedFile = null;
});

// テストから内部状態を確認できるように公開（本番動作には影響しない）
window.__scene = { state, render, refreshMatch, loadBackground };
