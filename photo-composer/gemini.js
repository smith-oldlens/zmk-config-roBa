// gemini.js — Google Gemini 画像生成プロバイダー
// providers.js から利用される。APIキーの保存/読込は providers.js 側で行う。

const MODEL = 'gemini-2.5-flash-image';
const ENDPOINT =
  `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent`;

// Gemini は透過画像を直接返せないため、緑背景で生成させてブラウザ側で
// クロマキー処理する（transparentOutput: false）。
function buildPrompt(userPrompt) {
  return (
    `写実的な写真。${userPrompt.trim()} ` +
    '全身が頭のてっぺんからつま先まで完全に写っている立ち姿で、体のどの部分も画面から見切れていない。' +
    '人物は画面の中央。背景は完全に均一で鮮やかなクロマキー用の緑一色（#00FF00）。' +
    '背景に影を落とさない。柔らかい自然光。' +
    'Full body photo of the person standing, head to toe fully visible, ' +
    'centered, on a perfectly uniform bright green chroma key background (#00FF00), ' +
    'no shadow cast on the background, soft natural light.'
  );
}

async function generate(apiKey, userPrompt) {
  let res;
  try {
    res = await fetch(ENDPOINT, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-goog-api-key': apiKey,
      },
      body: JSON.stringify({
        contents: [{ parts: [{ text: buildPrompt(userPrompt) }] }],
        generationConfig: {
          responseModalities: ['IMAGE'],
          imageConfig: { aspectRatio: '2:3' },
        },
      }),
    });
  } catch {
    throw new Error('ネットワークエラー：Gemini API に接続できませんでした。');
  }

  if (!res.ok) throw new Error(await describeError(res));

  const data = await res.json();
  const parts = data.candidates?.[0]?.content?.parts || [];
  const imagePart = parts.find((p) => p.inlineData?.data);
  if (!imagePart) {
    const reason = data.candidates?.[0]?.finishReason;
    throw new Error(
      reason === 'SAFETY' || reason === 'IMAGE_SAFETY'
        ? '安全性フィルタにより画像を生成できませんでした。プロンプトを変えてみてください。'
        : '画像が返されませんでした。もう一度試すか、プロンプトを変えてみてください。'
    );
  }
  const { mimeType, data: b64 } = imagePart.inlineData;
  return `data:${mimeType || 'image/png'};base64,${b64}`;
}

// 無料枠では画像生成の枠が0のため課金の有効化が必要（2025/12〜）。
// 429/400 いずれの形でも案内できるよう定数化。
const BILLING_MSG =
  '画像生成には有料枠（お支払い設定）の有効化が必要です。' +
  'Gemini の無料枠では画像を生成できません。' +
  'Google AI Studio でキーのプロジェクトの「お支払い情報」を有効にしてください。';

async function describeError(res) {
  let detail = '';
  try {
    const j = await res.json();
    detail = j.error?.message || '';
  } catch { /* 本文なし */ }
  // billing / 課金に言及するエラーはステータスに依らず優先して案内
  if (/billing|paid tier|free tier|plan and billing/i.test(detail)) return BILLING_MSG;
  switch (res.status) {
    case 400:
    case 403:
      if (/api key/i.test(detail)) return 'APIキーが無効です。設定画面で確認してください。';
      return `リクエストが拒否されました（${res.status}）。${detail}`;
    case 429:
      // 無料枠の画像生成は枠が0のため 429 になりやすい。まず課金を案内。
      return BILLING_MSG +
        '（すでに有効な場合は短時間のレート上限の可能性があるので、少し待って再試行してください）';
    case 500:
    case 503:
      return 'Gemini API が混雑しています。少し待ってから再試行してください。';
    default:
      return `生成に失敗しました（HTTP ${res.status}）。${detail}`;
  }
}

export const gemini = {
  id: 'gemini',
  label: 'Google Gemini',
  // 生成画像は緑背景 → クロマキーで透過化が必要
  transparentOutput: false,
  keyPlaceholder: 'AIza...',
  keyHelpUrl: 'https://aistudio.google.com/apikey',
  keyHelpLabel: 'Google AI Studio',
  // 旧バージョンの保存キーがあれば引き継ぐ
  legacyKeyStorage: 'sceneComposer.geminiApiKey',
  generate,
};
