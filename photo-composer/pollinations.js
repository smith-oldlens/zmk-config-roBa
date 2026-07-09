// pollinations.js — Pollinations.ai 画像生成プロバイダー（無料・APIキー不要）
// providers.js から利用される。
// Pollinations は透過画像を返さないため、緑背景で生成させてブラウザ側で
// クロマキー処理する（transparentOutput: false）。

const ENDPOINT = 'https://image.pollinations.ai/prompt/';

function buildPrompt(userPrompt) {
  return (
    `Photorealistic full-body photo of ${userPrompt.trim()}, ` +
    'standing, head to toe fully visible and not cropped, centered, ' +
    'on a perfectly uniform bright chroma key green background (#00FF00), ' +
    'no shadow on the background, soft natural light. ' +
    `写実的な全身写真：${userPrompt.trim()}。均一な緑背景。`
  );
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result);
    fr.onerror = () => reject(new Error('画像の読み込みに失敗しました。'));
    fr.readAsDataURL(blob);
  });
}

// APIキー不要。seed は呼ぶたびに変えて毎回違う結果になるようにする。
async function generate(_apiKey, userPrompt, seed) {
  const params = new URLSearchParams({
    width: '768',
    height: '1152',
    nologo: 'true',
    model: 'flux',
    seed: String(seed ?? Math.floor(Math.random() * 1e9)),
    // Webアプリを識別させる推奨パラメータ（キーは埋め込まない）
    referrer: 'scene-composer',
  });
  const url = ENDPOINT + encodeURIComponent(buildPrompt(userPrompt)) + '?' + params;

  let res;
  try {
    // fetch → blob → data URL にすることで、生成画像を data: 由来にして
    // キャンバスの汚染（getImageData 不可）を避け、クロマキー処理を可能にする。
    res = await fetch(url);
  } catch {
    throw new Error('ネットワークエラー：Pollinations に接続できませんでした。');
  }
  if (!res.ok) {
    if (res.status === 429) throw new Error('混雑しています。少し待ってから再試行してください。');
    throw new Error(`生成に失敗しました（HTTP ${res.status}）。時間を置いて再試行してください。`);
  }
  const blob = await res.blob();
  if (!blob.type.startsWith('image/')) {
    throw new Error('画像が返されませんでした。もう一度試してみてください。');
  }
  return blobToDataUrl(blob);
}

export const pollinations = {
  id: 'pollinations',
  label: 'Pollinations（無料・キー不要）',
  // APIキー不要
  noApiKey: true,
  // 緑背景で生成 → クロマキーで透過化が必要
  transparentOutput: false,
  generate,
};
