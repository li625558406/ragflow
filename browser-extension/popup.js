// RAGFlow Web Clipper — Popup Logic
'use strict';

const DEFAULT_SERVER = 'http://47.98.102.55:9380';

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

// --- Init ---
document.addEventListener('DOMContentLoaded', init);
clipBtn.addEventListener('click', handleClip);
optionsLink.addEventListener('click', () => chrome.runtime.openOptionsPage());

async function init() {
  // Load settings
  const stored = await chrome.storage.sync.get(['serverUrl', 'apiKey']);
  serverUrl = stored.serverUrl || DEFAULT_SERVER;
  apiKey = stored.apiKey || '';

  if (!apiKey) {
    showResult('error', '请先在设置中配置 API Key');
    return;
  }

  // Load KB list
  await loadKbList();
}

async function loadKbList() {
  showKbStatus('loading', '加载中...');

  try {
    const response = await chrome.runtime.sendMessage({
      type: 'FETCH_KB_LIST',
      serverUrl,
      apiKey,
    });

    if (response.error) {
      showKbStatus('error', response.error);
      return;
    }

    kbs = response.data || [];
    const suffix = response.cached ? ' (缓存)' : '';
    renderKbOptions();
    showKbStatus('success', `${kbs.length} 个知识库${suffix}`);
    clipBtn.disabled = false;
  } catch (err) {
    showKbStatus('error', `加载失败: ${err.message}`);
  }
}

function renderKbOptions() {
  // Keep first option ("-- 选择 --")
  kbSelect.innerHTML = '<option value="">-- 选择知识库 --</option>';
  kbs.forEach(kb => {
    const opt = document.createElement('option');
    opt.value = kb.kb_id;
    opt.textContent = `${kb.name} (${kb.chunk_num} chunks)`;
    kbSelect.appendChild(opt);
  });

  // Restore last selected KB
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

  // Save selection
  chrome.storage.local.set({ lastKbId: kbId });

  showResult('loading', '正在提取页面内容...');
  clipBtn.disabled = true;

  try {
    // Get current tab
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) {
      throw new Error('无法获取当前页面');
    }

    // Extract content
    const contentData = await chrome.runtime.sendMessage({
      type: 'EXTRACT_CONTENT',
    });

    if (!contentData || contentData.error) {
      throw new Error(contentData?.error || '内容提取失败');
    }

    if (!contentData.title && !contentData.content) {
      throw new Error('未提取到任何内容');
    }

    showResult('loading', '正在上传...');

    // Upload to KB
    const mode = parseMode.value;
    const payload = {
      title: contentData.title || '(无标题)',
      url: contentData.url || tab.url,
      parse_mode: mode,
    };

    if (mode === 'llm') {
      // For LLM mode, send raw HTML for better parsing
      payload.html = contentData.html;
    } else {
      // For naive mode, send cleaned text
      payload.content = contentData.content;
    }

    const result = await chrome.runtime.sendMessage({
      type: 'CLIP_TO_KB',
      serverUrl,
      apiKey,
      kbId,
      payload,
    });

    if (result.error) {
      throw new Error(result.error);
    }

    showResult('success', `采集成功 | doc_id: ${result.docId}`);
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
  if (type === 'loading') {
    kbStatus.className = 'status';
    kbStatus.textContent = message;
    kbStatus.classList.remove('hidden');
    kbStatus.style.color = '#888';
  } else if (type === 'success') {
    kbStatus.className = 'status';
    kbStatus.textContent = message;
    kbStatus.classList.remove('hidden');
    kbStatus.style.color = '#6ee7b7';
  } else {
    kbStatus.className = 'status';
    kbStatus.textContent = message;
    kbStatus.classList.remove('hidden');
    kbStatus.style.color = '#fca5a5';
  }
}
