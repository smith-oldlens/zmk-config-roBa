// compositor.js — 画像処理（クロマキー・色調マッチ・影・合成）
// すべてブラウザ内の Canvas で完結する。

export function createCanvas(w, h) {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  return c;
}

export function canvasFrom(source) {
  const c = createCanvas(source.width, source.height);
  c.getContext('2d').drawImage(source, 0, 0);
  return c;
}

// ---------------------------------------------------------------------------
// クロマキー
// ---------------------------------------------------------------------------

// 緑背景（グリーンバック）用キーイング。
// 緑の優勢度 diff = G - max(R, B) を使い、low〜high の間でソフトに抜く。
export function chromaKeyGreen(srcCanvas, { low = 14, high = 70 } = {}) {
  const c = canvasFrom(srcCanvas);
  const ctx = c.getContext('2d');
  const img = ctx.getImageData(0, 0, c.width, c.height);
  const d = img.data;
  for (let i = 0; i < d.length; i += 4) {
    const r = d[i], g = d[i + 1], b = d[i + 2];
    const maxRB = Math.max(r, b);
    const diff = g - maxRB;
    if (diff >= high) {
      d[i + 3] = 0;
    } else if (diff > low) {
      const t = (diff - low) / (high - low);
      d[i + 3] = Math.round(d[i + 3] * (1 - t));
      // 縁の緑かぶり除去（despill）
      d[i + 1] = Math.min(g, maxRB + low);
    } else if (diff > 0) {
      d[i + 1] = Math.min(g, maxRB + low);
    }
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

// 任意の色を基準にしたキーイング（緑抜きがうまくいかない場合のフォールバック）。
export function keyByColor(srcCanvas, key, tolerance = 60, softness = 40) {
  const c = canvasFrom(srcCanvas);
  const ctx = c.getContext('2d');
  const img = ctx.getImageData(0, 0, c.width, c.height);
  const d = img.data;
  for (let i = 0; i < d.length; i += 4) {
    const dr = d[i] - key.r, dg = d[i + 1] - key.g, db = d[i + 2] - key.b;
    const dist = Math.sqrt(dr * dr + dg * dg + db * db);
    if (dist <= tolerance) {
      d[i + 3] = 0;
    } else if (dist < tolerance + softness) {
      const t = (dist - tolerance) / softness;
      d[i + 3] = Math.round(d[i + 3] * t);
    }
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

// 透明部分を切り詰めて人物のバウンディングボックスだけ残す。
export function trimAlpha(canvas, threshold = 8) {
  const ctx = canvas.getContext('2d');
  const { width: w, height: h } = canvas;
  const d = ctx.getImageData(0, 0, w, h).data;
  let minX = w, minY = h, maxX = -1, maxY = -1;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (d[(y * w + x) * 4 + 3] > threshold) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < 0) return createCanvas(1, 1); // 全部透明 → 空の 1x1 を返す（呼び出し側が空と判定できる）
  const cw = maxX - minX + 1, ch = maxY - minY + 1;
  const out = createCanvas(cw, ch);
  out.getContext('2d').drawImage(canvas, minX, minY, cw, ch, 0, 0, cw, ch);
  return out;
}

// ---------------------------------------------------------------------------
// 色調マッチ（簡易 Reinhard color transfer）
// ---------------------------------------------------------------------------

// 矩形領域の RGB 平均・標準偏差（背景側の統計に使う）
export function regionStats(canvas, x, y, w, h) {
  const cw = canvas.width, chh = canvas.height;
  x = Math.max(0, Math.min(cw - 1, Math.round(x)));
  y = Math.max(0, Math.min(chh - 1, Math.round(y)));
  w = Math.max(1, Math.min(cw - x, Math.round(w)));
  h = Math.max(1, Math.min(chh - y, Math.round(h)));
  const d = canvas.getContext('2d').getImageData(x, y, w, h).data;
  return statsOf(d, () => true);
}

// 不透明ピクセルのみの統計（人物側）
export function alphaStats(canvas) {
  const d = canvas.getContext('2d')
    .getImageData(0, 0, canvas.width, canvas.height).data;
  return statsOf(d, (i) => d[i + 3] > 128);
}

function statsOf(d, include) {
  const sum = [0, 0, 0], sq = [0, 0, 0];
  let n = 0;
  for (let i = 0; i < d.length; i += 4) {
    if (!include(i)) continue;
    n++;
    for (let ch = 0; ch < 3; ch++) {
      const v = d[i + ch];
      sum[ch] += v;
      sq[ch] += v * v;
    }
  }
  if (n === 0) return { mean: [128, 128, 128], std: [50, 50, 50], n: 0 };
  const mean = sum.map((s) => s / n);
  const std = sq.map((s, ch) => Math.sqrt(Math.max(1, s / n - mean[ch] * mean[ch])));
  return { mean, std, n };
}

// 人物レイヤーの色味を背景統計に寄せた新しいキャンバスを返す。
// strength 0 = 元のまま、1 = 完全転写。
export function colorTransfer(srcCanvas, srcStats, dstStats, strength) {
  const c = canvasFrom(srcCanvas);
  if (strength <= 0.001) return c;
  const ctx = c.getContext('2d');
  const img = ctx.getImageData(0, 0, c.width, c.height);
  const d = img.data;
  const gain = [0, 1, 2].map((ch) => {
    const g = dstStats.std[ch] / Math.max(1e-3, srcStats.std[ch]);
    return Math.min(2.2, Math.max(0.45, g)); // 極端な変換を抑える
  });
  for (let i = 0; i < d.length; i += 4) {
    if (d[i + 3] === 0) continue;
    for (let ch = 0; ch < 3; ch++) {
      const v = d[i + ch];
      const t = (v - srcStats.mean[ch]) * gain[ch] + dstStats.mean[ch];
      d[i + ch] = Math.max(0, Math.min(255, v + (t - v) * strength));
    }
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

// ---------------------------------------------------------------------------
// 影・描画
// ---------------------------------------------------------------------------

// 人物のシルエット（黒塗り）キャンバス
export function makeSilhouette(canvas) {
  const c = createCanvas(canvas.width, canvas.height);
  const ctx = c.getContext('2d');
  ctx.drawImage(canvas, 0, 0);
  ctx.globalCompositeOperation = 'source-in';
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, c.width, c.height);
  return c;
}

const filterSupported = (() => {
  try {
    const ctx = createCanvas(1, 1).getContext('2d');
    ctx.filter = 'blur(1px)';
    return ctx.filter === 'blur(1px)';
  } catch {
    return false;
  }
})();

// ctx.filter 非対応ブラウザでは多重描画でぼかしを近似する
function withBlur(ctx, blurPx, draw) {
  if (blurPx < 0.3) { draw(); return; }
  if (filterSupported) {
    ctx.save();
    ctx.filter = `blur(${blurPx.toFixed(2)}px)`;
    draw();
    ctx.restore();
    return;
  }
  const steps = 6;
  const a = ctx.globalAlpha;
  ctx.save();
  ctx.globalAlpha = a / steps * 1.6;
  for (let k = 0; k < steps; k++) {
    const ang = (k / steps) * Math.PI * 2;
    ctx.save();
    ctx.translate(Math.cos(ang) * blurPx * 0.7, Math.sin(ang) * blurPx * 0.7);
    draw();
    ctx.restore();
  }
  ctx.restore();
}

// レイヤー1枚を ctx に描画する。
//   layer: { x, y, h, flipped, shadowOpacity, shadowBlur, shadowDir }
//     x, y = 足元中央（背景フル解像度座標）, h = 人物の高さ（同座標系）
//   person: 描画する人物キャンバス（色調マッチ済みでも可）
//   sil:    makeSilhouette() の結果
//   k:      背景フル解像度 → 描画先のスケール係数
export function drawLayer(ctx, layer, person, sil, k) {
  const scale = layer.h / person.height;
  const w = person.width * scale * k;
  const h = layer.h * k;
  const fx = layer.x * k;
  const fy = layer.y * k;

  // --- 影（潰したシルエット） ---
  if (layer.shadowOpacity > 0.01) {
    const blurPx = 2 + layer.shadowBlur * w * 0.30;
    ctx.save();
    ctx.globalAlpha = layer.shadowOpacity * 0.8;
    withBlur(ctx, blurPx, () => {
      ctx.save();
      ctx.translate(fx, fy);
      // y を反転・圧縮しつつ shadowDir 方向へシアー → 地面に落ちた影
      ctx.transform(layer.flipped ? -1 : 1, 0, layer.shadowDir, -0.30, 0, 0);
      ctx.drawImage(sil, -w / 2, -h, w, h);
      ctx.restore();
    });
    ctx.restore();

    // 足元の接地影（小さな楕円）
    ctx.save();
    ctx.globalAlpha = layer.shadowOpacity * 0.75;
    withBlur(ctx, Math.max(1.5, w * 0.06), () => {
      ctx.fillStyle = '#000';
      ctx.beginPath();
      ctx.ellipse(fx, fy, w * 0.30, Math.max(2, w * 0.06), 0, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.restore();
  }

  // --- 人物本体 ---
  ctx.save();
  ctx.translate(fx, fy);
  if (layer.flipped) ctx.scale(-1, 1);
  // 縮小率が大きいときは背景のボケ感に合わせて軽くぼかす。
  // 判定は k に依存しない scale（layer.h / person.height）で行い、
  // 実際のぼかし量だけ k を掛ける。こうするとプレビュー（k<1）と
  // 書き出し（k=1）でぼけ具合が一致する（WYSIWYG）。
  const blurNative = scale < 0.5 ? Math.min(1.2, (0.5 - scale) * 2) : 0;
  withBlur(ctx, blurNative * k, () => ctx.drawImage(person, -w / 2, -h, w, h));
  ctx.restore();
}

// レイヤーの矩形（描画先座標系）。ヒットテストや選択枠に使う。
export function layerRect(layer, person, k) {
  const scale = layer.h / person.height;
  const w = person.width * scale * k;
  const h = layer.h * k;
  return { x: layer.x * k - w / 2, y: (layer.y - layer.h) * k, w, h };
}

// シーン全体を合成する（プレビューにも書き出しにも使う）。
// bgCanvas は描画先と同じ解像度のものを渡す。
// k は「背景フル解像度座標 → 描画先ピクセル」の変換係数。
//   resolve(layer) → { person, sil }
export function renderScene(ctx, bgCanvas, layers, k, resolve) {
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  ctx.drawImage(bgCanvas, 0, 0);
  for (const layer of layers) {
    const { person, sil } = resolve(layer);
    drawLayer(ctx, layer, person, sil, k);
  }
}
