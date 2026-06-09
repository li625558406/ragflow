// RAGFlow Web Clipper — Popup Logic
'use strict';

const DEFAULT_SERVER = 'http://47.98.102.55:9380';
const FETCH_TIMEOUT = 10000; // 10 seconds

// UI elements
const kbSelect = document.getElementById('kbSelect');
const parseMode = document.getElementById('parseMode');
const clipBtn = document.getElementById('clipBtn');
const resultEl = document.getElementById('result');
const kbStatus = document.getElementById('kbStatus');
const optionsLink = document.getElementById('optionsLink');

// State
let serverUrl = DEFAULT_SERVER;
let apiKey = '';
let kbs = [];

// --- Helpers ---
async function apiFetch(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT);
  try {
    const resp = await fetch(`${serverUrl}/api/v1${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        'Authorization': apiKey,
        'Content-Type': 'application/json',
        ...(options.headers || {}),
      },
    });
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
    }
    return await resp.json();
  } finally {
    clearTimeout(timer);
  }
}

// --- Init ---
document.addEventListener('DOMContentLoaded', init);
clipBtn.addEventListener('click', handleClip);
optionsLink.addEventListener('click', () => chrome.runtime.openOptionsPage());

async function init() {
  const stored = await chrome.storage.local.get(['serverUrl', 'apiKey']);
  serverUrl = (stored.serverUrl || DEFAULT_SERVER).replace(/\/+$/, '');
  apiKey = stored.apiKey || '';

  if (!apiKey) {
    showResult('error', '请先在设置中配置 API Key');
    return;
  }

  await loadKbList();
}

async function loadKbList() {
  showKbStatus('loading', '加载中...');

  try {
    const result = await apiFetch('/kb_list');

    if (result.code !== 0) {
      showKbStatus('error', result.message || 'Unknown error');
      return;
    }

    kbs = result.data || [];
    renderKbOptions();
    showKbStatus('success', `${kbs.length} 个知识库`);
    clipBtn.disabled = false;
  } catch (err) {
    showKbStatus('error', `加载失败: ${err.message}`);
  }
}

function renderKbOptions() {
  kbSelect.innerHTML = '<option value="">-- 选择知识库 --</option>';
  kbs.forEach(kb => {
    const opt = document.createElement('option');
    opt.value = kb.kb_id;
    opt.textContent = `${kb.name} (${kb.chunk_num} chunks)`;
    kbSelect.appendChild(opt);
  });

  chrome.storage.local.get(['lastKbId'], (result) => {
    if (result.lastKbId && kbSelect.querySelector(`option[value="${result.lastKbId}"]`)) {
      kbSelect.value = result.lastKbId;
    }
  });
}

async function handleClip() {
  const kbId = kbSelect.value;
  if (!kbId) {
    showResult('error', '请选择目标知识库');
    return;
  }

  chrome.storage.local.set({ lastKbId: kbId });

  showResult('loading', '正在提取页面内容...');
  clipBtn.disabled = true;

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) {
      throw new Error('无法获取当前页面');
    }

    const contentData = await chrome.runtime.sendMessage({
      type: 'EXTRACT_CONTENT',
      tabId: tab.id,
    });

    if (!contentData || contentData.error) {
      throw new Error(contentData?.error || '内容提取失败');
    }

    if (!contentData.title && !contentData.content) {
      throw new Error('未提取到任何内容');
    }

    showResult('loading', '正在上传...');

    const mode = parseMode.value;
    const payload = {
      title: contentData.title || '(无标题)',
      url: contentData.url || tab.url,
      parse_mode: mode,
    };

    if (mode === 'llm') {
      payload.html = contentData.html;
    } else {
      payload.content = contentData.content;
    }

    const result = await apiFetch(`/kb/${kbId}/clip`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });

    if (result.code !== 0) {
      throw new Error(result.message || '上传失败');
    }

    showResult('success', `采集成功 | doc_id: ${result.data.doc_id}`);
  } catch (err) {
    showResult('error', err.message);
  } finally {
    clipBtn.disabled = false;
  }
}

function showResult(type, message) {
  resultEl.className = `result ${type}`;
  resultEl.textContent = message;
  resultEl.classList.remove('hidden');
}

function showKbStatus(type, message) {
  kbStatus.className = 'status';
  kbStatus.textContent = message;
  kbStatus.classList.remove('hidden');
  if (type === 'loading') {
    kbStatus.style.color = '#6b7280';
  } else if (type === 'success') {
    kbStatus.style.color = '#065f46';
  } else {
    kbStatus.style.color = '#dc2626';
  }
}
