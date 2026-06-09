// RAGFlow Web Clipper — Options Page
'use strict';

const DEFAULT_SERVER = 'http://47.98.102.55:9380';

const serverEl = document.getElementById('serverUrl');
const apiKeyEl = document.getElementById('apiKey');
const saveBtn = document.getElementById('saveBtn');
const msgEl = document.getElementById('msg');

document.addEventListener('DOMContentLoaded', init);
saveBtn.addEventListener('click', save);

async function init() {
  const stored = await chrome.storage.sync.get(['serverUrl', 'apiKey']);
  serverEl.value = stored.serverUrl || DEFAULT_SERVER;
  apiKeyEl.value = stored.apiKey || '';
}

async function save() {
  const serverUrl = serverEl.value.trim();
  const apiKey = apiKeyEl.value.trim();

  if (!serverUrl) {
    showMsg('请输入服务器地址');
    return;
  }
  if (!apiKey) {
    showMsg('请输入 API Key');
    return;
  }

  await chrome.storage.sync.set({ serverUrl, apiKey });
  showMsg('已保存', true);
}

function showMsg(text, isSuccess) {
  msgEl.textContent = text;
  msgEl.className = isSuccess ? 'success' : '';
  msgEl.classList.remove('hidden');
  setTimeout(() => msgEl.classList.add('hidden'), 3000);
}
