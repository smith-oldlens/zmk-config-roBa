// openai.js — OpenAI gpt-image-1 画像生成プロバイダー
// providers.js から利用される。
// gpt-image-1 は background:'transparent' で透過PNGを直接返せるため、
// クロマキー処理は不要（transparentOutput: true）。

const ENDPOINT = 'https://api.openai.com/v1/images/generations';

function buildPrompt(userPrompt) {
  return (
    `A photorealistic full-body photo of ${userPrompt.trim()}, ` +
    'standing, head to toe fully visible and not cropped, centered, ' +
    'on a fully transparent background (no background, no ground, no shadow), soft natural light. ' +
    `写実的な人物写真：${userPrompt.trim()}。全身が頭からつま先まで写り、見切れていない立ち姿。背景は完全に透明。`
  );
}

async function generate(apiKey, userPrompt) {
  let res;
  try {
    res = await fetch(ENDPOINT, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model: 'gpt-image-1',
        prompt: buildPrompt(userPrompt),
        size: '1024x1536',
        background: 'transparent',
        output_format: 'png',
        n: 1,
      }),
    });
  } catch {
    throw new Error('ネットワークエラー：OpenAI API に接続できませんでした。');
  }

  if (!res.ok) throw new Error(await describeError(res));

  const data = await res.json();
  const b64 = data.data?.[0]?.b64_json;
  if (!b64) throw new Error('画像が返されませんでした。もう一度試してみてください。');
  return `data:image/png;base64,${b64}`;
}

async function describeError(res) {
  let detail = '';
  try {
    const j = await res.json();
    detail = j.error?.message || '';
  } catch { /* 本文なし */ }
  switch (res.status) {
    case 401:
      return 'APIキーが無効です。設定画面で確認してください。';
    case 403:
      // 組織未認証（gpt-image-1 は本人確認が必要な場合がある）
      if (/verif/i.test(detail)) return '組織の本人確認が必要です。OpenAIの管理画面で Verify Organization を完了してください。';
      return `リクエストが拒否されました（403）。${detail}`;
    case 429:
      return '利用上限（レート/クォータ）に達しました。しばらく待つか、請求設定を確認してください。';
    case 500:
    case 503:
      return 'OpenAI API が混雑しています。少し待ってから再試行してください。';
    default:
      return `生成に失敗しました（HTTP ${res.status}）。${detail}`;
  }
}

export const openai = {
  id: 'openai',
  label: 'OpenAI (gpt-image-1)',
  // 透過PNGを直接返すのでクロマキー不要
  transparentOutput: true,
  keyPlaceholder: 'sk-...',
  keyHelpUrl: 'https://platform.openai.com/api-keys',
  keyHelpLabel: 'OpenAI ダッシュボード',
  generate,
};
