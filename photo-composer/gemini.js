// gemini.js — Gemini 画像生成 API（人物素材の生成）
// APIキーは localStorage にのみ保存し、Google の API 以外へは送信しない。

const MODEL = 'gemini-2.5-flash-image';
const ENDPOINT =
  `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent`;
const KEY_STORAGE = 'sceneComposer.geminiApiKey';

export function getApiKey() {
  try { return localStorage.getItem(KEY_STORAGE) || ''; } catch { return ''; }
}

export function setApiKey(key) {
  try {
    if (key) localStorage.setItem(KEY_STORAGE, key.trim());
    else localStorage.removeItem(KEY_STORAGE);
  } catch { /* プライベートモード等では保存できないが動作は継続 */ }
}

// プリセット（UIの選択肢）
export const PRESETS = [
  { label: '白ワンピの女性（後ろ姿）', prompt: '白いロングワンピースを着た若い女性が立っている後ろ姿。長い黒髪。' },
  { label: '白ワンピの女性（横向き）', prompt: '白いロングワンピースを着た若い女性が横を向いて立っている。長い黒髪。' },
  { label: '白ワンピ＋透明の傘', prompt: '白いロングワンピースを着た若い女性が透明のビニール傘をさして立っている。' },
  { label: '麦わら帽子の子ども', prompt: '麦わら帽子をかぶった小さな子どもが立っている後ろ姿。' },
];

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

// 人物画像を生成して data URL を返す。
export async function generatePerson(apiKey, userPrompt) {
  if (!apiKey) throw new Error('APIキーが設定されていません。設定画面から入力してください。');
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

async function describeError(res) {
  let detail = '';
  try {
    const j = await res.json();
    detail = j.error?.message || '';
  } catch { /* 本文なし */ }
  switch (res.status) {
    case 400:
    case 403:
      if (/api key/i.test(detail)) return 'APIキーが無効です。設定画面で確認してください。';
      return `リクエストが拒否されました（${res.status}）。${detail}`;
    case 429:
      return '利用上限（クォータ）に達しました。しばらく待ってから再試行してください。';
    case 500:
    case 503:
      return 'Gemini API が混雑しています。少し待ってから再試行してください。';
    default:
      return `生成に失敗しました（HTTP ${res.status}）。${detail}`;
  }
}
