// providers.js — 画像生成プロバイダーの登録・切り替え・APIキー管理
// app.js はこのモジュール経由でのみ生成バックエンドを扱う。
// 新しいプロバイダーを追加するときは、gemini.js/openai.js と同じ形の
// プロバイダーオブジェクトを作って PROVIDERS に足すだけでよい。
import { gemini } from './gemini.js';
// OpenAI (gpt-image-1) は実装済みだが、組織の本人確認・課金設定が
// 済むまで一旦無効化している。有効化するには下の2行のコメントを外すだけでよい。
// import { openai } from './openai.js';

export const PROVIDERS = [gemini/*, openai */];

const PROVIDER_STORAGE = 'sceneComposer.provider';
const keyStorageOf = (id) => `sceneComposer.apiKey.${id}`;

function safeGet(k) { try { return localStorage.getItem(k) || ''; } catch { return ''; } }
function safeSet(k, v) {
  try { if (v) localStorage.setItem(k, v); else localStorage.removeItem(k); } catch { /* 保存不可でも継続 */ }
}

export function getProviderId() {
  const id = safeGet(PROVIDER_STORAGE);
  return PROVIDERS.some((p) => p.id === id) ? id : PROVIDERS[0].id;
}

export function setProviderId(id) {
  if (PROVIDERS.some((p) => p.id === id)) safeSet(PROVIDER_STORAGE, id);
}

export function getProvider(id = getProviderId()) {
  return PROVIDERS.find((p) => p.id === id) || PROVIDERS[0];
}

export function getApiKey(id = getProviderId()) {
  const key = safeGet(keyStorageOf(id));
  if (key) return key;
  // 旧バージョンの保存キーからの移行
  const legacy = getProvider(id).legacyKeyStorage;
  return legacy ? safeGet(legacy) : '';
}

export function setApiKey(id, key) {
  safeSet(keyStorageOf(id), (key || '').trim());
}

// 現在のプロバイダーで人物画像を生成し、data URL を返す。
export async function generatePerson(userPrompt) {
  const provider = getProvider();
  const apiKey = getApiKey(provider.id);
  if (!apiKey) throw new Error('APIキーが設定されていません。設定画面から入力してください。');
  return provider.generate(apiKey, userPrompt);
}

// プリセット（UIの選択肢）— プロバイダー非依存の人物説明
export const PRESETS = [
  { label: '白ワンピの女性（後ろ姿）', prompt: '白いロングワンピースを着た若い女性が立っている後ろ姿。長い黒髪。' },
  { label: '白ワンピの女性（横向き）', prompt: '白いロングワンピースを着た若い女性が横を向いて立っている。長い黒髪。' },
  { label: '白ワンピ＋透明の傘', prompt: '白いロングワンピースを着た若い女性が透明のビニール傘をさして立っている。' },
  { label: '麦わら帽子の子ども', prompt: '麦わら帽子をかぶった小さな子どもが立っている後ろ姿。' },
];
